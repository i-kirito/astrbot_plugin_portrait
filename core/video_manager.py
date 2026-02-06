"""
视频缓存管理器

用于在需要以本地文件方式发送时，下载 Grok 返回的视频并进行简单清理。
支持在线视频URL存储，用于画廊在线播放。
"""

from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from pathlib import Path
from typing import Any

import aiofiles
import httpx

from astrbot.api import logger


def _clamp_int(value: Any, *, default: int, min_value: int, max_value: int) -> int:
    try:
        value_int = int(value)
    except (TypeError, ValueError):
        return default
    return max(min_value, min(max_value, value_int))


class VideoManager:
    def __init__(self, config: dict, data_dir: Path):
        self.config = config
        storage = config.get("storage", {}) if isinstance(config, dict) else {}

        self.video_dir = data_dir / "videos"
        self.video_dir.mkdir(parents=True, exist_ok=True)

        self._metadata_path = data_dir / "video_metadata.json"
        self._metadata: dict = {}
        self._load_metadata()

        self.max_cached_videos: int = _clamp_int(
            (storage.get("max_cached_videos") if isinstance(storage, dict) else None)
            or config.get("max_cached_videos", 20),
            default=20,
            min_value=0,
            max_value=500,
        )
        self.cleanup_batch_ratio = 0.5

    def _load_metadata(self) -> None:
        """加载视频元数据"""
        try:
            if self._metadata_path.exists():
                self._metadata = json.loads(self._metadata_path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning(f"[VideoManager] 加载元数据失败: {e}")
            self._metadata = {}

    def _save_metadata(self) -> None:
        """保存视频元数据"""
        try:
            self._metadata_path.write_text(
                json.dumps(self._metadata, ensure_ascii=False, indent=2),
                encoding="utf-8"
            )
        except Exception as e:
            logger.warning(f"[VideoManager] 保存元数据失败: {e}")

    def save_video_url(self, url: str, prompt: str = "") -> str:
        """保存视频URL到元数据，返回视频ID"""
        video_id = f"{int(time.time())}_{uuid.uuid4().hex[:8]}"
        self._metadata[video_id] = {
            "url": url,
            "prompt": prompt,
            "created_at": int(time.time()),
        }
        self._save_metadata()
        self._cleanup_old_metadata()
        logger.debug(f"[VideoManager] 已保存视频URL: {video_id}")
        return video_id

    def get_video_list(self) -> list[dict]:
        """获取所有视频列表"""
        videos = []
        for video_id, meta in self._metadata.items():
            videos.append({
                "id": video_id,
                "url": meta.get("url", ""),
                "prompt": meta.get("prompt", ""),
                "created_at": meta.get("created_at", 0),
            })
        # 按创建时间倒序
        videos.sort(key=lambda x: x["created_at"], reverse=True)
        return videos

    def delete_video(self, video_id: str) -> bool:
        """删除视频"""
        if video_id in self._metadata:
            del self._metadata[video_id]
            self._save_metadata()
            return True
        return False

    def _cleanup_old_metadata(self) -> None:
        """清理旧的视频元数据"""
        if self.max_cached_videos <= 0:
            return

        if len(self._metadata) <= self.max_cached_videos:
            return

        # 按创建时间排序，删除最旧的
        items = list(self._metadata.items())
        items.sort(key=lambda x: x[1].get("created_at", 0))

        delete_count = len(items) - self.max_cached_videos
        for i in range(delete_count):
            del self._metadata[items[i][0]]

        self._save_metadata()
        logger.debug(f"[VideoManager] 清理旧视频元数据: 删除={delete_count}")

    async def download_video(self, url: str, *, timeout_seconds: int = 300) -> Path:
        if not url:
            raise ValueError("缺少视频 URL")

        timeout_seconds = max(1, min(int(timeout_seconds), 3600))
        filename = f"{int(time.time())}_{uuid.uuid4().hex[:8]}.mp4"
        path = self.video_dir / filename

        timeout = httpx.Timeout(
            connect=10.0,
            read=float(timeout_seconds),
            write=10.0,
            pool=float(timeout_seconds) + 10.0,
        )

        t0 = time.perf_counter()
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            async with client.stream("GET", url) as resp:
                resp.raise_for_status()
                async with aiofiles.open(path, "wb") as f:
                    async for chunk in resp.aiter_bytes(chunk_size=1024 * 256):
                        await f.write(chunk)

        logger.info(
            f"[VideoManager] 下载完成: path={path}, 耗时={time.perf_counter() - t0:.2f}s"
        )

        await self.cleanup_old_videos()
        return path

    async def cleanup_old_videos(self) -> None:
        if self.max_cached_videos <= 0:
            return

        try:
            videos: list[Path] = list(self.video_dir.iterdir())
            total = len(videos)
            if total <= self.max_cached_videos:
                return

            overflow = total - self.max_cached_videos
            delete_count = max(1, int(overflow * self.cleanup_batch_ratio))

            stats = await asyncio.gather(
                *[asyncio.to_thread(p.stat) for p in videos],
                return_exceptions=True,
            )

            valid: list[tuple[Path, float]] = []
            for p, st in zip(videos, stats):
                if isinstance(st, os.stat_result):
                    valid.append((p, st.st_mtime))

            valid.sort(key=lambda x: x[1])  # old -> new
            to_delete = valid[:delete_count]

            await asyncio.gather(
                *[asyncio.to_thread(p.unlink) for p, _ in to_delete],
                return_exceptions=True,
            )

            logger.debug(
                f"[VideoManager] 清理旧视频: 删除={len(to_delete)}, 当前={total - len(to_delete)}"
            )

        except Exception as e:
            logger.warning(f"[VideoManager] 清理旧视频失败: {e}")
