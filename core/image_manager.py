"""图片管理器 - 处理图片下载和保存"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import ipaddress
import json
import socket
import time
from pathlib import Path
from urllib.parse import urlparse

import aiohttp
from astrbot.api import logger

from .image_format import guess_image_mime_and_ext

# 最大下载大小：20MB
MAX_DOWNLOAD_SIZE = 20 * 1024 * 1024


def _is_safe_url(url: str) -> bool:
    """检查URL是否安全（防止SSRF）"""
    # 如果已经是本地路径，直接返回安全
    url_str = str(url)
    if url_str.startswith('/AstrBot/data/'):
        return True

    # 可信域名 white名单
    trusted_domains = (
        '.bcebos.com',      # 百度云存储（Gitee AI 使用）
        '.baidubce.com',    # 百度云
        '.gitee.com',       # Gitee
    )

    try:
        parsed = urlparse(url_str)
        # 允许 http/https
        if parsed.scheme not in ('http', 'https'):
            return False
        host = parsed.hostname
        if not host:
            return False

        host_lower = host.lower()

        # 检查是否为可信域名
        if any(host_lower.endswith(domain) for domain in trusted_domains):
            return True

        # 先检查是否直接是 IP 地址
        try:
            ip = ipaddress.ip_address(host)
            if ip.is_private or ip.is_reserved or ip.is_loopback or ip.is_link_local:
                return False
        except ValueError:
            # 域名解析验证
            try:
                addr_infos = socket.getaddrinfo(host, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
                for addr_info in addr_infos:
                    ip_str = addr_info[4][0]
                    try:
                        ip = ipaddress.ip_address(ip_str)
                        if ip.is_private or ip.is_reserved or ip.is_loopback or ip.is_link_local:
                            logger.warning(f"[SSRF] 域名 {host} 解析到不安全 IP: {ip_str}")
                            return False
                    except ValueError:
                        continue
            except socket.gaierror:
                logger.warning(f"[SSRF] 无法解析域名: {host}")
                return False

        return True
    except Exception as e:
        logger.warning(f"[SSRF] URL 安全检查异常: {e}")
        return False


class ImageManager:
    """图片管理器"""

    def __init__(
        self,
        data_dir: Path,
        proxy: str | None = None,
        max_storage_mb: int = 500,
        max_count: int = 1000,
    ):
        self.data_dir = Path(data_dir)
        self.images_dir = self.data_dir / "generated_images"
        self.images_dir.mkdir(parents=True, exist_ok=True)
        self.metadata_file = self.data_dir / "image_metadata.json"
        self.favorites_file = self.data_dir / "favorites.json"
        self.proxy = proxy
        self.max_storage_mb = max_storage_mb
        self.max_count = max_count
        self._session: aiohttp.ClientSession | None = None
        # 延迟加载
        self._metadata: dict = {}
        self._metadata_loaded: bool = False
        self._metadata_mtime: float = 0.0
        self._favorites: set = set()
        self._favorites_loaded: bool = False
        # 并发锁
        self._metadata_lock = asyncio.Lock()
        self._favorites_lock = asyncio.Lock()
        self._session_lock = asyncio.Lock()

    def _ensure_metadata_loaded(self) -> None:
        if not self._metadata_loaded:
            self._metadata = self._load_metadata()
            self._metadata_mtime = self._get_metadata_mtime()
            self._metadata_loaded = True

    def _ensure_favorites_loaded(self) -> None:
        if not self._favorites_loaded:
            self._favorites = self._load_favorites()
            self._favorites_loaded = True

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is not None and not self._session.closed:
            return self._session
        async with self._session_lock:
            if self._session is not None and not self._session.closed:
                return self._session
            timeout = aiohttp.ClientTimeout(total=60, connect=10)
            self._session = aiohttp.ClientSession(timeout=timeout)
        return self._session

    def _load_metadata(self) -> dict:
        if self.metadata_file.exists():
            try:
                with open(self.metadata_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"[ImageManager] 加载元数据失败: {e}")
        return {}

    def _get_metadata_mtime(self) -> float:
        try:
            if self.metadata_file.exists():
                return self.metadata_file.stat().st_mtime
        except Exception:
            pass
        return 0.0

    def _reload_metadata_if_changed(self) -> None:
        self._ensure_metadata_loaded()
        current_mtime = self._get_metadata_mtime()
        if current_mtime > self._metadata_mtime:
            self._metadata = self._load_metadata()
            self._metadata_mtime = current_mtime

    def _save_metadata_sync(self) -> None:
        try:
            with open(self.metadata_file, "w", encoding="utf-8") as f:
                json.dump(self._metadata, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"[ImageManager] 保存元数据失败: {e}")

    def _load_favorites(self) -> set:
        if self.favorites_file.exists():
            try:
                with open(self.favorites_file, "r", encoding="utf-8") as f:
                    return set(json.load(f))
            except Exception as e:
                logger.warning(f"[ImageManager] 加载收藏列表失败: {e}")
        return set()

    def _save_favorites_sync(self) -> None:
        try:
            with open(self.favorites_file, "w", encoding="utf-8") as f:
                json.dump(list(self._favorites), f, ensure_ascii=False)
        except Exception as e:
            logger.error(f"[ImageManager] 保存收藏列表失败: {e}")

    async def get_metadata_async(self, filename: str) -> dict | None:
        async with self._metadata_lock:
            current_mtime = await asyncio.to_thread(self._get_metadata_mtime)
            if current_mtime > self._metadata_mtime:
                self._metadata = await asyncio.to_thread(self._load_metadata)
                self._metadata_mtime = current_mtime
            return self._metadata.get(filename)

    async def get_metadata_snapshot_async(self) -> dict:
        """获取元数据快照"""
        async with self._metadata_lock:
            if not self._metadata_loaded:
                self._metadata = await asyncio.to_thread(self._load_metadata)
                self._metadata_mtime = self._get_metadata_mtime()
                self._metadata_loaded = True
            else:
                current_mtime = await asyncio.to_thread(self._get_metadata_mtime)
                if current_mtime > self._metadata_mtime:
                    self._metadata = await asyncio.to_thread(self._load_metadata)
                    self._metadata_mtime = current_mtime
            return dict(self._metadata)

    async def get_favorites_snapshot_async(self) -> set[str]:
        """获取收藏快照"""
        async with self._favorites_lock:
            if not self._favorites_loaded:
                self._favorites = await asyncio.to_thread(self._load_favorites)
                self._favorites_loaded = True
            return set(self._favorites)

    async def set_metadata_async(
        self,
        filename: str,
        prompt: str,
        *,
        model: str = "",
        category: str = "",
        size: str = "",
    ) -> None:
        async with self._metadata_lock:
            if not self._metadata_loaded:
                self._metadata = await asyncio.to_thread(self._load_metadata)
                self._metadata_mtime = self._get_metadata_mtime()
                self._metadata_loaded = True
            self._metadata[filename] = {
                "prompt": prompt,
                "created_at": int(time.time()),
                "model": model,
                "category": category,
                "size": size,
            }
            await asyncio.to_thread(self._save_metadata_sync)
            self._metadata_mtime = self._get_metadata_mtime()

    async def remove_metadata_async(self, filename: str) -> None:
        async with self._metadata_lock:
            async with self._favorites_lock:
                if not self._metadata_loaded:
                    self._metadata = await asyncio.to_thread(self._load_metadata)
                    self._metadata_mtime = self._get_metadata_mtime()
                    self._metadata_loaded = True
                if not self._favorites_loaded:
                    self._favorites = await asyncio.to_thread(self._load_favorites)
                    self._favorites_loaded = True
                self._metadata.pop(filename, None)
                self._favorites.discard(filename)
                await asyncio.to_thread(self._save_metadata_sync)
                await asyncio.to_thread(self._save_favorites_sync)
                self._metadata_mtime = self._get_metadata_mtime()

    async def set_metadata_batch_async(
        self,
        items: list[tuple[str, str, str, str, str]],
    ) -> None:
        """批量写入元数据（单次落盘）

        Args:
            items: [(filename, prompt, model, category, size), ...]
        """
        if not items:
            return
        async with self._metadata_lock:
            self._metadata = await asyncio.to_thread(self._load_metadata)
            self._metadata_mtime = self._get_metadata_mtime()
            self._metadata_loaded = True
            now = int(time.time())
            for filename, prompt, model, category, size_val in items:
                old = self._metadata.get(filename, {})
                self._metadata[filename] = {
                    "prompt": prompt or old.get("prompt", ""),
                    "created_at": old.get("created_at", now),
                    "model": model or old.get("model", ""),
                    "category": category or old.get("category", ""),
                    "size": size_val or old.get("size", ""),
                }
            await asyncio.to_thread(self._save_metadata_sync)
            self._metadata_mtime = self._get_metadata_mtime()

    async def remove_metadata_batch_async(self, filenames: list[str]) -> None:
        """批量删除元数据与收藏（单次落盘）"""
        if not filenames:
            return
        names = set(filenames)
        async with self._metadata_lock:
            async with self._favorites_lock:
                self._metadata = await asyncio.to_thread(self._load_metadata)
                self._metadata_mtime = self._get_metadata_mtime()
                self._metadata_loaded = True
                if not self._favorites_loaded:
                    self._favorites = await asyncio.to_thread(self._load_favorites)
                    self._favorites_loaded = True

                changed = False
                for filename in names:
                    if filename in self._metadata:
                        self._metadata.pop(filename, None)
                        changed = True
                    if filename in self._favorites:
                        self._favorites.discard(filename)
                        changed = True

                if changed:
                    await asyncio.to_thread(self._save_metadata_sync)
                    await asyncio.to_thread(self._save_favorites_sync)
                    self._metadata_mtime = self._get_metadata_mtime()

    async def toggle_favorite_async(self, filename: str) -> bool:
        async with self._favorites_lock:
            if not self._favorites_loaded:
                self._favorites = await asyncio.to_thread(self._load_favorites)
                self._favorites_loaded = True
            if filename in self._favorites:
                self._favorites.discard(filename)
                await asyncio.to_thread(self._save_favorites_sync)
                return False
            else:
                self._favorites.add(filename)
                await asyncio.to_thread(self._save_favorites_sync)
                return True

    async def download_image(
        self,
        url: str,
        prompt: str = "",
        *,
        model: str = "",
        category: str = "",
        size: str = "",
    ) -> Path:
        """下载图片并保存到本地"""
        # SSRF防护
        if not _is_safe_url(url):
            raise ValueError(f"不安全的URL: {url}")

        session = await self._get_session()
        try:
            # 手动处理重定向
            max_redirects = 3
            current_url = str(url)
            for _ in range(max_redirects + 1):
                async with session.get(current_url, proxy=self.proxy, allow_redirects=False) as resp:
                    if resp.status in (301, 302, 303, 307, 308):
                        redirect_url = resp.headers.get('Location')
                        if not redirect_url: raise ValueError("缺少 Location")
                        if not _is_safe_url(redirect_url): raise ValueError("重定向不安全")
                        current_url = redirect_url
                        continue
                    resp.raise_for_status()
                    data = await resp.read()
                    break
            else:
                raise ValueError("重定向过多")
        except Exception as e:
            logger.error(f"[ImageManager] 下载图片失败: {e}")
            raise

        _, ext = guess_image_mime_and_ext(data)
        md5_hash = await asyncio.to_thread(lambda: hashlib.md5(data).hexdigest()[:8])
        filename = f"{int(time.time() * 1000)}_{md5_hash}.{ext}"
        path = self.images_dir / filename

        await asyncio.to_thread(path.write_bytes, data)
        
        # 统一分类逻辑
        if not category:
            category = "龙虾"
        if not model:
            model = "Gitee-AI"

        await self.set_metadata_async(filename, prompt, model=model, category=category, size=size)
        return path

    async def save_image_bytes(
        self,
        data: bytes,
        prompt: str = "",
        *,
        model: str = "",
        category: str = "",
        size: str = "",
    ) -> Path:
        _, ext = guess_image_mime_and_ext(data)
        md5_hash = await asyncio.to_thread(lambda: hashlib.md5(data).hexdigest()[:8])
        filename = f"{int(time.time() * 1000)}_{md5_hash}.{ext}"
        path = self.images_dir / filename
        await asyncio.to_thread(path.write_bytes, data)
        
        if not category:
            category = "龙虾"
        if not model:
            model = "Gitee-AI"

        await self.set_metadata_async(filename, prompt, model=model, category=category, size=size)
        return path

    async def save_base64_image(self, b64_data: str, prompt: str = "", **kwargs) -> Path:
        if "," in b64_data: b64_data = b64_data.split(",", 1)[1]
        data = base64.b64decode(b64_data)
        return await self.save_image_bytes(data, prompt, **kwargs)

    async def cleanup_old_images(self) -> int:
        """清理旧图片"""
        if self.max_storage_mb <= 0 and self.max_count <= 0:
            return 0
        async with self._favorites_lock:
            if not self._favorites_loaded:
                self._favorites = await asyncio.to_thread(self._load_favorites)
                self._favorites_loaded = True

        try:
            allowed_exts = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
            def _scan_images():
                res = []
                for fp in self.images_dir.iterdir():
                    if fp.is_file() and fp.suffix.lower() in allowed_exts:
                        if fp.name in self._favorites: continue
                        s = fp.stat()
                        res.append((fp, s.st_size, s.st_mtime))
                return res
            images = await asyncio.to_thread(_scan_images)
            if not images: return 0
            images.sort(key=lambda x: x[2])
            total_size = sum(img[1] for img in images)
            total_count = len(images)
            max_size_bytes = self.max_storage_mb * 1024 * 1024
            deleted = 0
            for path, size, _ in images:
                if (self.max_count > 0 and total_count > self.max_count) or (self.max_storage_mb > 0 and total_size > max_size_bytes):
                    try:
                        await asyncio.to_thread(path.unlink)
                        await self.remove_metadata_async(path.name)
                        total_size -= size
                        total_count -= 1
                        deleted += 1
                    except Exception: pass
            return deleted
        except Exception as e:
            logger.error(f"[ImageManager] 清理失败: {e}")
            return 0
