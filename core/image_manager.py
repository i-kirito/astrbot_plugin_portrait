"""图片管理器 - 处理图片下载和保存"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import time
from pathlib import Path

import aiohttp
from astrbot.api import logger


def guess_image_ext(data: bytes) -> str:
    """根据图片数据头判断扩展名"""
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if data[:2] == b"\xff\xd8":
        return "jpg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return "png"


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
        self._metadata: dict = self._load_metadata()
        self._metadata_mtime: float = self._get_metadata_mtime()
        self._favorites: set = self._load_favorites()
        # 并发锁：保护元数据和收藏文件的读写
        self._metadata_lock = asyncio.Lock()
        self._favorites_lock = asyncio.Lock()

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
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
        current_mtime = self._get_metadata_mtime()
        if current_mtime > self._metadata_mtime:
            self._metadata = self._load_metadata()
            self._metadata_mtime = current_mtime
        return {}

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

    def set_metadata(self, filename: str, prompt: str) -> None:
        """设置图片元数据"""
        self._metadata[filename] = {
            "prompt": prompt,
            "created_at": int(time.time()),
        }
        self._save_metadata()
        self._metadata_mtime = self._get_metadata_mtime()

    async def set_metadata_async(self, filename: str, prompt: str) -> None:
        """设置图片元数据（异步版本，带锁，非阻塞 I/O）"""
        async with self._metadata_lock:
            self._metadata[filename] = {
                "prompt": prompt,
                "created_at": int(time.time()),
            }
            await asyncio.to_thread(self._save_metadata_sync)
            self._metadata_mtime = self._get_metadata_mtime()

    def is_favorite(self, filename: str) -> bool:
        """检查是否为收藏"""
        return filename in self._favorites

    def toggle_favorite(self, filename: str) -> bool:
        """切换收藏状态，返回新状态"""
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
        self._metadata.pop(filename, None)
        self._favorites.discard(filename)
        self._save_metadata()
        self._save_favorites()

    async def remove_metadata_async(self, filename: str) -> None:
        """删除图片元数据（异步版本，带锁，非阻塞 I/O）"""
        async with self._metadata_lock:
            async with self._favorites_lock:
                self._metadata.pop(filename, None)
                self._favorites.discard(filename)
                await asyncio.to_thread(self._save_metadata_sync)
                await asyncio.to_thread(self._save_favorites_sync)

    async def close(self) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None

    async def download_image(self, url: str, prompt: str = "") -> Path:
        """下载图片并保存到本地"""
        session = await self._get_session()
        try:
            async with session.get(url, proxy=self.proxy, timeout=60) as resp:
                resp.raise_for_status()
                data = await resp.read()
        except Exception as e:
            logger.error(f"[ImageManager] 下载图片失败: {e}")
            raise

        ext = guess_image_ext(data)
        filename = f"{int(time.time() * 1000)}_{hashlib.md5(data).hexdigest()[:8]}.{ext}"
        path = self.images_dir / filename

        await asyncio.to_thread(path.write_bytes, data)
        if prompt:
            await self.set_metadata_async(filename, prompt)
        logger.debug(f"[ImageManager] 图片已保存: {path}")
        return path

    async def save_base64_image(self, b64_data: str, prompt: str = "") -> Path:
        """保存 base64 编码的图片"""
        # 处理 data URL 格式
        if "," in b64_data:
            b64_data = b64_data.split(",", 1)[1]

        data = base64.b64decode(b64_data)
        return await self.save_image_bytes(data, prompt)

    async def save_image_bytes(self, data: bytes, prompt: str = "") -> Path:
        """保存字节数据的图片"""
        ext = guess_image_ext(data)
        filename = f"{int(time.time() * 1000)}_{hashlib.md5(data).hexdigest()[:8]}.{ext}"
        path = self.images_dir / filename

        await asyncio.to_thread(path.write_bytes, data)
        if prompt:
            await self.set_metadata_async(filename, prompt)
        logger.debug(f"[ImageManager] 图片已保存: {path}")
        return path

    async def cleanup_old_images(self) -> int:
        """清理旧图片（跳过收藏），返回删除数量"""
        if self.max_storage_mb <= 0 and self.max_count <= 0:
            return 0  # 不限制

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

            # 按数量清理
            if self.max_count > 0 and total_count > self.max_count:
                excess_count = total_count - self.max_count
                for i in range(excess_count):
                    to_delete.append(images[i][0])
                    total_size -= images[i][1]

            # 按大小清理
            if self.max_storage_mb > 0 and total_size > max_size_bytes:
                idx = len(to_delete)
                while total_size > max_size_bytes and idx < len(images):
                    if images[idx][0] not in to_delete:
                        to_delete.append(images[idx][0])
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
