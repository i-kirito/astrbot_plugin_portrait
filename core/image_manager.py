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
    """检查URL是否安全（防止SSRF）

    采用 DNS 解析验证，防止以下绕过方式：
    - 127.0.0.1.nip.io (DNS 重绑定)
    - [::1] (IPv6 回环)
    - 2130706433 (十进制 IP)
    - 0x7f000001 (十六进制 IP)
    """
    # 可信域名白名单（跳过 IP 检查）
    trusted_domains = (
        '.bcebos.com',      # 百度云存储（Gitee AI 使用）
        '.baidubce.com',    # 百度云
        '.gitee.com',       # Gitee
    )

    try:
        parsed = urlparse(url)
        # 允许 http/https（内部使用场景允许 http，如 Gitee AI 返回的图片链接）
        if parsed.scheme not in ('http', 'https'):
            return False
        host = parsed.hostname
        if not host:
            return False

        host_lower = host.lower()

        # 检查是否为可信域名（跳过 IP 检查）
        if any(host_lower.endswith(domain) for domain in trusted_domains):
            return True

        # 先检查是否直接是 IP 地址
        try:
            ip = ipaddress.ip_address(host)
            if ip.is_private or ip.is_reserved or ip.is_loopback or ip.is_link_local:
                return False
        except ValueError:
            # 不是 IP，是域名，进行 DNS 解析验证

            # 快速拒绝已知危险域名
            dangerous_patterns = [
                'localhost', 'internal', 'intranet', 'corp',
                '169.254.', 'metadata', 'instance'
            ]
            if any(pat in host_lower for pat in dangerous_patterns):
                return False
            if host_lower.endswith('.local'):
                return False

            # DNS 解析获取真实 IP 并验证
            try:
                # 解析所有 IP 地址（IPv4 + IPv6）
                addr_infos = socket.getaddrinfo(host, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
                for addr_info in addr_infos:
                    ip_str = addr_info[4][0]
                    try:
                        ip = ipaddress.ip_address(ip_str)
                        # 任何一个解析结果指向私网/回环/链路本地都拒绝
                        if ip.is_private or ip.is_reserved or ip.is_loopback or ip.is_link_local:
                            logger.warning(f"[SSRF] 域名 {host} 解析到不安全 IP: {ip_str}")
                            return False
                    except ValueError:
                        continue
            except socket.gaierror:
                # DNS 解析失败，拒绝
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
        max_count: int = 100,
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
        # 延迟加载：避免阻塞启动
        self._metadata: dict = {}
        self._metadata_loaded: bool = False
        self._metadata_mtime: float = 0.0
        self._favorites: set = set()
        self._favorites_loaded: bool = False
        # 并发锁：保护元数据和收藏文件的读写
        self._metadata_lock = asyncio.Lock()
        self._favorites_lock = asyncio.Lock()
        self._session_lock = asyncio.Lock()  # 保护 session 创建

    def _ensure_metadata_loaded(self) -> None:
        """确保元数据已加载（延迟加载）"""
        if not self._metadata_loaded:
            self._metadata = self._load_metadata()
            self._metadata_mtime = self._get_metadata_mtime()
            self._metadata_loaded = True

    def _ensure_favorites_loaded(self) -> None:
        """确保收藏已加载（延迟加载）"""
        if not self._favorites_loaded:
            self._favorites = self._load_favorites()
            self._favorites_loaded = True

    async def _get_session(self) -> aiohttp.ClientSession:
        """获取或创建 HTTP 会话（带锁保护避免并发重复创建）"""
        # 快速路径：已有可用 session
        if self._session is not None and not self._session.closed:
            return self._session

        # 慢速路径：需要创建，加锁保护
        async with self._session_lock:
            # 双重检查
            if self._session is not None and not self._session.closed:
                return self._session

            timeout = aiohttp.ClientTimeout(total=60, connect=10)
            self._session = aiohttp.ClientSession(timeout=timeout)
        return self._session

    def _load_metadata(self) -> dict:
        """加载图片元数据"""
        if self.metadata_file.exists():
            try:
                with open(self.metadata_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"[ImageManager] 加载元数据失败: {e}")
        return {}

    def _get_metadata_mtime(self) -> float:
        """获取元数据文件修改时间"""
        try:
            if self.metadata_file.exists():
                return self.metadata_file.stat().st_mtime
        except Exception:
            pass
        return 0.0

    def _reload_metadata_if_changed(self) -> None:
        """如果文件已修改则重新加载元数据"""
        self._ensure_metadata_loaded()
        current_mtime = self._get_metadata_mtime()
        if current_mtime > self._metadata_mtime:
            self._metadata = self._load_metadata()
            self._metadata_mtime = current_mtime

    def _save_metadata_sync(self) -> None:
        """保存图片元数据（同步版本，内部使用）"""
        try:
            with open(self.metadata_file, "w", encoding="utf-8") as f:
                json.dump(self._metadata, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"[ImageManager] 保存元数据失败: {e}")

    def _save_metadata(self) -> None:
        """保存图片元数据（兼容旧接口）"""
        self._save_metadata_sync()

    def _load_favorites(self) -> set:
        """加载收藏列表"""
        if self.favorites_file.exists():
            try:
                with open(self.favorites_file, "r", encoding="utf-8") as f:
                    return set(json.load(f))
            except Exception as e:
                logger.warning(f"[ImageManager] 加载收藏列表失败: {e}")
        return set()

    def _save_favorites_sync(self) -> None:
        """保存收藏列表（同步版本，内部使用）"""
        try:
            with open(self.favorites_file, "w", encoding="utf-8") as f:
                json.dump(list(self._favorites), f, ensure_ascii=False)
        except Exception as e:
            logger.error(f"[ImageManager] 保存收藏列表失败: {e}")

    def _save_favorites(self) -> None:
        """保存收藏列表（兼容旧接口）"""
        self._save_favorites_sync()

    def get_metadata(self, filename: str) -> dict | None:
        """获取图片元数据"""
        # 检查文件是否被修改，按需重新加载
        self._reload_metadata_if_changed()
        return self._metadata.get(filename)

    async def get_metadata_async(self, filename: str) -> dict | None:
        """获取图片元数据（异步版本，非阻塞 I/O）"""
        async with self._metadata_lock:
            # 异步检查并重载元数据
            current_mtime = await asyncio.to_thread(self._get_metadata_mtime)
            if current_mtime > self._metadata_mtime:
                self._metadata = await asyncio.to_thread(self._load_metadata)
                self._metadata_mtime = current_mtime
            return self._metadata.get(filename)

    async def get_metadata_snapshot_async(self) -> dict:
        """获取元数据快照（单次重载，避免循环内反复 stat）"""
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
        """获取收藏快照（避免扫描循环内反复读取）"""
        async with self._favorites_lock:
            if not self._favorites_loaded:
                self._favorites = await asyncio.to_thread(self._load_favorites)
                self._favorites_loaded = True
            return set(self._favorites)

    def set_metadata(
        self,
        filename: str,
        prompt: str,
        *,
        model: str = "",
        category: str = "",
        size: str = "",
    ) -> None:
        """设置图片元数据"""
        self._ensure_metadata_loaded()
        self._metadata[filename] = {
            "prompt": prompt,
            "created_at": int(time.time()),
            "model": model,
            "category": category,
            "size": size,
        }
        self._save_metadata()
        self._metadata_mtime = self._get_metadata_mtime()

    async def set_metadata_async(
        self,
        filename: str,
        prompt: str,
        *,
        model: str = "",
        category: str = "",
        size: str = "",
    ) -> None:
        """设置图片元数据（异步版本，带锁，非阻塞 I/O）"""
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
            if not self._metadata_loaded:
                self._metadata = await asyncio.to_thread(self._load_metadata)
                self._metadata_mtime = self._get_metadata_mtime()
                self._metadata_loaded = True
            now = int(time.time())
            for filename, prompt, model, category, size in items:
                old = self._metadata.get(filename, {})
                self._metadata[filename] = {
                    "prompt": prompt,
                    "created_at": old.get("created_at", now),
                    "model": model,
                    "category": category,
                    "size": size,
                }
            await asyncio.to_thread(self._save_metadata_sync)
            self._metadata_mtime = self._get_metadata_mtime()

    def update_metadata_no_save(self, filename: str, **kwargs) -> None:
        """更新元数据但不立即保存（需手动调用保存，用于批量更新）"""
        self._ensure_metadata_loaded()
        if filename not in self._metadata:
            # 如果不存在，初始化基本结构
            self._metadata[filename] = {
                "created_at": int(time.time()),
                "prompt": "",
                "model": "",
                "category": "",
                "size": ""
            }
        self._metadata[filename].update(kwargs)

    async def save_metadata_async(self) -> None:
        """异步保存元数据（配合 update_metadata_no_save 使用）"""
        async with self._metadata_lock:
            await asyncio.to_thread(self._save_metadata_sync)
            self._metadata_mtime = self._get_metadata_mtime()

    def is_favorite(self, filename: str) -> bool:
        """检查是否为收藏"""
        self._ensure_favorites_loaded()
        return filename in self._favorites

    def toggle_favorite(self, filename: str) -> bool:
        """切换收藏状态，返回新状态"""
        self._ensure_favorites_loaded()
        if filename in self._favorites:
            self._favorites.discard(filename)
            self._save_favorites()
            return False
        else:
            self._favorites.add(filename)
            self._save_favorites()
            return True

    async def toggle_favorite_async(self, filename: str) -> bool:
        """切换收藏状态（异步版本，带锁，非阻塞 I/O）"""
        async with self._favorites_lock:
            # 延迟加载收藏
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

    def remove_metadata(self, filename: str) -> None:
        """删除图片元数据"""
        self._ensure_metadata_loaded()
        self._ensure_favorites_loaded()
        self._metadata.pop(filename, None)
        self._favorites.discard(filename)
        self._save_metadata()
        self._save_favorites()

    async def remove_metadata_async(self, filename: str) -> None:
        """删除图片元数据（异步版本，带锁，非阻塞 I/O）"""
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

    async def remove_metadata_batch_async(self, filenames: list[str]) -> None:
        """批量删除元数据与收藏（单次落盘）"""
        if not filenames:
            return
        names = set(filenames)
        async with self._metadata_lock:
            async with self._favorites_lock:
                if not self._metadata_loaded:
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

    async def close(self) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None

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
        # SSRF防护：验证URL安全性
        if not _is_safe_url(url):
            raise ValueError(f"不安全的URL: {url}")

        session = await self._get_session()
        try:
            # 禁用自动重定向，手动处理以防止 SSRF 重定向绕过
            max_redirects = 3
            current_url = url
            for _ in range(max_redirects + 1):
                async with session.get(current_url, proxy=self.proxy, allow_redirects=False) as resp:
                    # 处理重定向
                    if resp.status in (301, 302, 303, 307, 308):
                        redirect_url = resp.headers.get('Location')
                        if not redirect_url:
                            raise ValueError("重定向响应缺少 Location 头")
                        # 对重定向目标进行 SSRF 校验
                        if not _is_safe_url(redirect_url):
                            raise ValueError(f"重定向目标不安全: {redirect_url}")
                        current_url = redirect_url
                        continue

                    resp.raise_for_status()
                    # 检查Content-Length（如果有）
                    content_length = resp.headers.get('Content-Length')
                    if content_length and int(content_length) > MAX_DOWNLOAD_SIZE:
                        raise ValueError(f"文件过大: {int(content_length) / 1024 / 1024:.1f}MB > {MAX_DOWNLOAD_SIZE / 1024 / 1024:.0f}MB")
                    # 流式读取并限制大小（使用 bytearray 降低内存峰值）
                    data = bytearray()
                    total_size = 0
                    async for chunk in resp.content.iter_chunked(8192):
                        total_size += len(chunk)
                        if total_size > MAX_DOWNLOAD_SIZE:
                            raise ValueError(f"文件过大: 超过{MAX_DOWNLOAD_SIZE / 1024 / 1024:.0f}MB限制")
                        data.extend(chunk)
                    data = bytes(data)
                    break
            else:
                raise ValueError(f"重定向次数过多 (>{max_redirects})")
        except ValueError:
            raise
        except Exception as e:
            logger.error(f"[ImageManager] 下载图片失败: {e}")
            raise

        _, ext = guess_image_mime_and_ext(data)
        # 将 MD5 计算移至线程池避免阻塞
        md5_hash = await asyncio.to_thread(lambda: hashlib.md5(data).hexdigest()[:8])
        filename = f"{int(time.time() * 1000)}_{md5_hash}.{ext}"
        path = self.images_dir / filename

        await asyncio.to_thread(path.write_bytes, data)

        # 统一分类逻辑：如果 prompt 中包含特定关键词，分类为“龙虾”
        if not category and prompt:
            prompt_lower = prompt.lower()
            lobster_keywords = ["lobster", "龙虾", "小龙虾", "kitten", "小猫", "猫", "cat"]
            if any(kw in prompt_lower for kw in lobster_keywords):
                category = "龙虾"

        if prompt or model or category or size:
            await self.set_metadata_async(
                filename, prompt, model=model, category=category, size=size
            )
        logger.debug(f"[ImageManager] 图片已保存: {path}")
        return path

    async def save_base64_image(
        self,
        b64_data: str,
        prompt: str = "",
        *,
        model: str = "",
        category: str = "",
        size: str = "",
    ) -> Path:
        """保存 base64 编码的图片"""
        # 处理 data URL 格式
        if "," in b64_data:
            b64_data = b64_data.split(",", 1)[1]

        data = base64.b64decode(b64_data)
        return await self.save_image_bytes(
            data, prompt, model=model, category=category, size=size
        )

    async def save_image_bytes(
        self,
        data: bytes,
        prompt: str = "",
        *,
        model: str = "",
        category: str = "",
        size: str = "",
    ) -> Path:
        """保存字节数据的图片"""
        _, ext = guess_image_mime_and_ext(data)
        # 将 MD5 计算移至线程池避免阻塞
        md5_hash = await asyncio.to_thread(lambda: hashlib.md5(data).hexdigest()[:8])
        filename = f"{int(time.time() * 1000)}_{md5_hash}.{ext}"
        path = self.images_dir / filename

        await asyncio.to_thread(path.write_bytes, data)
        
        # 统一分类逻辑：如果 prompt 中包含特定关键词，分类为“龙虾”
        if not category and prompt:
            prompt_lower = prompt.lower()
            lobster_keywords = ["lobster", "龙虾", "小龙虾", "kitten", "小猫", "猫", "cat"]
            if any(kw in prompt_lower for kw in lobster_keywords):
                category = "龙虾"

        if prompt or model or category or size:
            await self.set_metadata_async(
                filename, prompt, model=model, category=category, size=size
            )
        logger.debug(f"[ImageManager] 图片已保存: {path}")
        return path

    async def cleanup_old_images(self) -> int:
        """清理旧图片（跳过收藏），返回删除数量"""
        if self.max_storage_mb <= 0 and self.max_count <= 0:
            return 0  # 不限制

        # 确保收藏列表已加载，避免误删
        async with self._favorites_lock:
            if not self._favorites_loaded:
                self._favorites = await asyncio.to_thread(self._load_favorites)
                self._favorites_loaded = True

        try:
            allowed_exts = {".png", ".jpg", ".jpeg", ".gif", ".webp"}

            def _scan_images() -> list[tuple[Path, int, float]]:
                """扫描图片（同步，在线程池执行）"""
                result = []
                for file_path in self.images_dir.iterdir():
                    if file_path.is_file() and file_path.suffix.lower() in allowed_exts:
                        if file_path.name in self._favorites:
                            continue
                        stat = file_path.stat()
                        result.append((file_path, stat.st_size, stat.st_mtime))
                return result

            # 在线程池中扫描文件
            images = await asyncio.to_thread(_scan_images)

            if not images:
                return 0

            # 按修改时间排序（旧的在前）
            images.sort(key=lambda x: x[2])

            total_size = sum(img[1] for img in images)
            total_count = len(images)
            max_size_bytes = self.max_storage_mb * 1024 * 1024

            to_delete: list[Path] = []
            to_delete_set: set[Path] = set()

            # 按数量清理
            if self.max_count > 0 and total_count > self.max_count:
                excess_count = total_count - self.max_count
                for i in range(excess_count):
                    to_delete.append(images[i][0])
                    to_delete_set.add(images[i][0])
                    total_size -= images[i][1]

            # 按大小清理
            if self.max_storage_mb > 0 and total_size > max_size_bytes:
                idx = len(to_delete)
                while total_size > max_size_bytes and idx < len(images):
                    if images[idx][0] not in to_delete_set:
                        to_delete.append(images[idx][0])
                        to_delete_set.add(images[idx][0])
                        total_size -= images[idx][1]
                    idx += 1

            # 执行删除（使用异步版本避免竞态）
            deleted = 0
            for path in to_delete:
                try:
                    await asyncio.to_thread(path.unlink)
                    await self.remove_metadata_async(path.name)
                    deleted += 1
                except Exception as e:
                    logger.warning(f"[ImageManager] 删除图片失败 {path.name}: {e}")

            if deleted > 0:
                logger.info(f"[ImageManager] 已清理 {deleted} 张旧图片")

            return deleted

        except Exception as e:
            logger.error(f"[ImageManager] 清理旧图片出错: {e}")
            return 0
