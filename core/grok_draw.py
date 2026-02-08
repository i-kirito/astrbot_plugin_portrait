"""
Grok 图片生成服务（/v1/chat/completions）

基于 Grok chat completions 接口生成图片，支持：
- 纯文生图（generate）
- 参考图改图（edit）
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import json
import random
import re
import time
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx

from astrbot.api import logger


def _guess_image_mime(data: bytes) -> str:
    """根据文件头猜测 MIME 类型"""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    return "image/jpeg"


def _guess_ext(mime: str) -> str:
    """根据 MIME 类型获取扩展名"""
    mapping = {
        "image/png": "png",
        "image/jpeg": "jpg",
        "image/gif": "gif",
        "image/webp": "webp",
    }
    return mapping.get(mime, "jpg")


def _origin(url: str) -> str:
    """提取 URL 的 origin 部分"""
    try:
        u = urlsplit(url)
        if u.scheme and u.netloc:
            return f"{u.scheme}://{u.netloc}"
    except Exception:
        pass
    return ""


def _normalize_base_url(base_url: str) -> str:
    """标准化 base_url"""
    url = (base_url or "").strip().rstrip("/")
    if not url:
        return ""
    return url


def _build_data_url(image_bytes: bytes) -> str:
    """构建 data URL"""
    mime = _guess_image_mime(image_bytes)
    b64 = base64.b64encode(image_bytes).decode("utf-8")
    return f"data:{mime};base64,{b64}"


def _parse_sse_response(text: str) -> dict[str, Any]:
    """解析 SSE (Server-Sent Events) 流式响应，合并为完整的 chat completion 格式"""
    accumulated_content = ""
    last_chunk: dict[str, Any] = {}

    for line in text.split("\n"):
        line = line.strip()
        if not line or line == "data: [DONE]":
            continue
        if line.startswith("data:"):
            json_str = line[5:].strip()
            if not json_str:
                continue
            try:
                chunk = json.loads(json_str)
                last_chunk = chunk
                # 提取 delta.content
                choices = chunk.get("choices", [])
                if choices:
                    delta = choices[0].get("delta", {})
                    content = delta.get("content", "")
                    if content:
                        accumulated_content += content
            except json.JSONDecodeError:
                continue

    # 构造完整的响应格式
    if accumulated_content or last_chunk:
        return {
            "id": last_chunk.get("id", ""),
            "object": "chat.completion",
            "model": last_chunk.get("model", ""),
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": accumulated_content,
                    },
                    "finish_reason": "stop",
                }
            ],
        }
    return {}


def _is_valid_image_url(url: str, *, from_img_tag: bool = False) -> bool:
    """验证图片 URL 是否有效"""
    if not isinstance(url, str):
        return False
    url = url.strip()
    if len(url) < 10:
        return False
    if not url.startswith(("http://", "https://")):
        return False
    if any(c in url for c in ["<", ">", '"', "'", "\n", "\r", "\t"]):
        return False

    lowered = url.lower()

    # 排除视频 URL
    if any(ext in lowered for ext in (".mp4", ".webm", ".mov")):
        return False
    if "generated_video" in lowered:
        return False

    # 检查标准图片扩展名
    has_image_ext = any(ext in lowered for ext in (".png", ".jpg", ".jpeg", ".gif", ".webp"))
    if has_image_ext:
        return True

    # 从 <img> 标签提取的 URL 可信度高
    if from_img_tag:
        return True

    # 检查是否包含 generated_image 或 image 相关路径
    if "generated_image" in lowered or "/image" in lowered:
        return True

    # 检查 base64 编码的路径（某些代理会编码路径）
    if "/images/p_" in url:
        return True

    return False


def _extract_image_url_from_content(content: str) -> str | None:
    """从响应内容中提取图片 URL"""
    if not content:
        return None

    # HTML <img src="...">
    if "<img" in content and "src=" in content:
        html_patterns = [
            r'<img[^>]*src=["\']([^"\'>\s]+)["\'][^>]*>',
            r'src=["\']([^"\'>\s]+)["\']',
        ]
        for pattern in html_patterns:
            match = re.search(pattern, content, re.IGNORECASE)
            if match:
                url = match.group(1).strip()
                if _is_valid_image_url(url, from_img_tag=True):
                    return url

    # Markdown 图片格式 ![...](url)
    md_pattern = r'!\[[^\]]*\]\(([^)\s]+)\)'
    match = re.search(md_pattern, content)
    if match:
        url = match.group(1).strip()
        if _is_valid_image_url(url, from_img_tag=True):
            return url

    # 直接 URL 匹配
    url_pattern = r'(https?://[^\s<>"\']+\.(?:png|jpg|jpeg|gif|webp)(?:\?[^\s<>"\']*)?)'
    match = re.search(url_pattern, content, re.IGNORECASE)
    if match:
        url = match.group(1).strip()
        if _is_valid_image_url(url):
            return url

    # 宽松的 URL 匹配（某些代理返回的 URL 不含扩展名）
    loose_pattern = r'(https?://[^\s<>"\']+/images/[^\s<>"\']+)'
    match = re.search(loose_pattern, content, re.IGNORECASE)
    if match:
        url = match.group(1).strip()
        if _is_valid_image_url(url, from_img_tag=True):
            return url

    return None


def _extract_image_url_from_response(response_data: Any) -> tuple[str | None, str | None]:
    """从 API 响应中提取图片 URL

    Returns: (image_url, error_message)
    """
    try:
        if not isinstance(response_data, dict):
            return None, f"无效的响应格式: {type(response_data).__name__}"

        choices = response_data.get("choices")
        if not isinstance(choices, list) or not choices:
            return None, "API 响应缺少 choices"

        choice0 = choices[0]
        if not isinstance(choice0, dict):
            return None, "choices[0] 格式错误"

        message = choice0.get("message")
        if not isinstance(message, dict):
            return None, "choices[0] 缺少 message"

        content = message.get("content")
        if isinstance(content, str):
            url = _extract_image_url_from_content(content)
            if url:
                return url, None

        content_preview = ""
        if isinstance(content, str):
            content_preview = content[:200]
        logger.warning(
            f"[GrokDraw] 未能提取图片 URL，content 片段: {content_preview}..."
        )
        return None, "未能从 API 响应中提取到有效的图片 URL"
    except Exception as e:
        logger.warning(f"[GrokDraw] URL 提取异常: {e}")
        return None, f"URL 提取失败: {e}"


class GrokDrawService:
    """Grok 图片生成服务（使用 chat completions API）"""

    def __init__(
        self,
        *,
        data_dir: Path,
        api_key: str = "",
        base_url: str = "https://api.x.ai",
        model: str = "grok-2-image",
        default_size: str = "1024x1024",
        timeout: int = 120,
        max_retries: int = 2,
        proxy: str | None = None,
        max_storage_mb: int = 500,
        max_count: int = 100,
    ):
        self.data_dir = data_dir
        self.api_key = (api_key or "").strip()
        self.base_url = _normalize_base_url(base_url)
        self.model = (model or "grok-2-image").strip()
        self.default_size = (default_size or "1024x1024").strip()
        self.timeout = max(30, min(int(timeout or 120), 600))
        self.max_retries = max(0, min(int(max_retries or 2), 10))
        self.proxy = (proxy or "").strip() or None
        self.max_storage_mb = max_storage_mb
        self.max_count = max_count

        # 图片存储目录
        self.image_dir = self.data_dir / "generated_images"
        self.image_dir.mkdir(parents=True, exist_ok=True)

        # 使用 chat completions 端点（用于改图）
        self._endpoint = f"{self.base_url}/v1/chat/completions" if self.base_url else ""
        # 使用 images generations 端点（用于纯文生图，支持自定义尺寸）
        self._images_endpoint = f"{self.base_url}/v1/images/generations" if self.base_url else ""
        self._origin = _origin(self._endpoint)
        self._cleanup_task: asyncio.Task | None = None
        # 共享的 HTTP 客户端（连接池复用）
        self._client: httpx.AsyncClient | None = None
        self._client_lock = asyncio.Lock()  # 保护 client 创建，避免并发重复创建

    async def _get_client(self) -> httpx.AsyncClient:
        """获取或创建共享的 HTTP 客户端（带锁保护避免并发重复创建）"""
        # 快速路径：已有可用 client
        if self._client is not None and not self._client.is_closed:
            return self._client

        # 慢速路径：需要创建，加锁保护
        async with self._client_lock:
            # 双重检查
            if self._client is not None and not self._client.is_closed:
                return self._client

            timeout = httpx.Timeout(
                timeout=float(self.timeout),
                connect=min(10.0, float(self.timeout)),
            )
            limits = httpx.Limits(
                max_connections=20,
                max_keepalive_connections=10,
                keepalive_expiry=30.0,
            )
            self._client = httpx.AsyncClient(
                timeout=timeout,
                limits=limits,
                follow_redirects=True,
                proxy=self.proxy,
            )
        return self._client

    async def close(self) -> None:
        """关闭 HTTP 客户端"""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None
        # 同时关闭后台清理任务
        if self._cleanup_task and not self._cleanup_task.done():
            self._cleanup_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._cleanup_task
        self._cleanup_task = None

    @property
    def enabled(self) -> bool:
        """是否已配置"""
        return bool(self.api_key and self._endpoint)

    def _headers(self) -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

    async def generate(
        self,
        prompt: str,
        images: list[bytes] | None = None,
        *,
        size: str | None = None,
        resolution: str | None = None,
    ) -> Path:
        """生成图片

        Args:
            prompt: 提示词
            images: 参考图片列表（可选，用于改图）
            size: 图片尺寸（纯文生图时有效）
            resolution: 分辨率快捷方式（1K/2K/4K）

        Returns:
            生成的图片路径
        """
        if not self.enabled:
            raise RuntimeError("Grok 图片服务未配置 API Key")

        final_prompt = (prompt or "").strip() or "a high quality image"

        # 解析尺寸
        final_size = size or self.default_size
        if resolution:
            size_map = {"1K": "1024x1024", "2K": "2048x2048", "4K": "4096x4096"}
            final_size = size_map.get(resolution.upper(), final_size)

        logger.info(f"[GrokDraw] 尺寸参数: size={size}, default_size={self.default_size}, resolution={resolution}, final_size={final_size}")

        # 根据是否有参考图选择不同的 API
        if images:
            # 有参考图：使用 chat completions API（不支持自定义尺寸）
            if final_size != "1024x1024":
                logger.warning(f"[GrokDraw] 注意：有参考图时使用 chat API，不支持自定义尺寸 {final_size}，将使用 API 默认尺寸")
            return await self._generate_with_chat(final_prompt, images)
        else:
            # 纯文生图：使用 images generations API（支持自定义尺寸）
            return await self._generate_with_images_api(final_prompt, final_size)

    async def _generate_with_images_api(self, prompt: str, size: str) -> Path:
        """使用 /v1/images/generations API 生成图片（支持自定义尺寸）"""
        payload = {
            "model": self.model,
            "prompt": prompt,
            "n": 1,
            "size": size,
            "response_format": "url",
        }

        logger.info(f"[GrokDraw] 使用 Images API: endpoint={self._images_endpoint}, size={size}, model={self.model}")

        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                t0 = time.perf_counter()
                client = await self._get_client()
                resp = await client.post(
                    self._images_endpoint, headers=self._headers(), json=payload
                )

                if resp.status_code != 200:
                    raise RuntimeError(
                        f"Grok Images API 失败 HTTP {resp.status_code}: {resp.text[:300]}"
                    )

                data = resp.json()
                # 从 images/generations 响应中提取 URL
                image_data = data.get("data", [])
                if not image_data:
                    raise RuntimeError("Grok Images API 未返回图片数据")

                image_url = image_data[0].get("url")
                if not image_url:
                    # 尝试获取 b64_json
                    b64_data = image_data[0].get("b64_json")
                    if b64_data:
                        return await self._save_b64(b64_data)
                    raise RuntimeError("Grok Images API 未返回图片 URL")

                logger.info(
                    "[GrokDraw] Images API 生成成功, 尺寸: %s, 耗时: %.2fs",
                    size,
                    time.perf_counter() - t0,
                )
                return await self._save_ref(image_url)

            except Exception as e:
                last_error = e
                if attempt < self.max_retries:
                    logger.warning(
                        "[GrokDraw] Images API 第 %d 次尝试失败: %s，重试中...",
                        attempt + 1,
                        e,
                    )
                    sleep_s = min(0.5 * (2 ** attempt), 4.0) + random.random() * 0.2
                    await asyncio.sleep(sleep_s)
                    continue

        raise RuntimeError(f"Grok 图片生成失败: {last_error}")

    async def _generate_with_chat(self, prompt: str, images: list[bytes]) -> Path:
        """使用 /v1/chat/completions API 生成图片（支持参考图改图）"""

        # 构建 chat completions 请求
        content: list[dict[str, Any]] = [
            {"type": "text", "text": prompt},
        ]

        # 添加参考图到请求
        for img_bytes in images[:4]:  # 最多 4 张参考图
            image_data_url = _build_data_url(img_bytes)
            content.append({
                "type": "image_url",
                "image_url": {"url": image_data_url},
            })

        payload = {
            "model": self.model,
            "stream": False,
            "messages": [
                {
                    "role": "user",
                    "content": content,
                }
            ],
        }

        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                t0 = time.perf_counter()
                client = await self._get_client()
                resp = await client.post(
                    self._endpoint, headers=self._headers(), json=payload
                )

                if resp.status_code != 200:
                    raise RuntimeError(
                        f"Grok API 失败 HTTP {resp.status_code}: {resp.text[:300]}"
                    )

                # 检测是否为 SSE 流式响应
                response_text = resp.text
                if response_text.startswith("data:"):
                    logger.debug("[GrokDraw] 检测到 SSE 流式响应，正在解析...")
                    data = _parse_sse_response(response_text)
                else:
                    try:
                        data = resp.json()
                    except Exception as e:
                        raise RuntimeError(
                            f"API 响应 JSON 解析失败: {e}, body={resp.text[:200]}"
                        ) from e

                image_url, parse_error = _extract_image_url_from_response(data)
                if not image_url:
                    raise RuntimeError(f"Grok 未返回图片: {parse_error}")

                logger.info(
                    "[GrokDraw] 生成成功, 耗时: %.2fs, url: %s...",
                    time.perf_counter() - t0,
                    image_url[:80],
                )
                return await self._save_ref(image_url)

            except Exception as e:
                last_error = e
                if attempt < self.max_retries:
                    logger.warning(
                        "[GrokDraw] 第 %d 次尝试失败: %s，重试中...",
                        attempt + 1,
                        e,
                    )
                    sleep_s = min(0.5 * (2 ** attempt), 4.0) + random.random() * 0.2
                    await asyncio.sleep(sleep_s)
                    continue
                raise

        raise RuntimeError(f"Grok 图片生成失败: {last_error}") from last_error

    async def _save_b64(self, b64_data: str) -> Path:
        """保存 base64 编码的图片到本地"""
        image_bytes = base64.b64decode((b64_data or "").strip())
        return await self._save_bytes(image_bytes)

    async def _save_ref(self, ref: str) -> Path:
        """保存图片引用（URL 或 base64）到本地"""
        ref = (ref or "").strip()
        if not ref:
            raise RuntimeError("空图片引用")

        # Base64 数据
        if ref.startswith("data:image/"):
            try:
                _header, b64_data = ref.split(",", 1)
            except ValueError:
                raise RuntimeError("data:image 缺少 base64 数据") from None
            image_bytes = base64.b64decode((b64_data or "").strip())
            return await self._save_bytes(image_bytes)

        # HTTP URL
        if ref.startswith(("http://", "https://")):
            return await self._download_image(ref)

        # 相对 URL
        if self._origin and ref.startswith("/"):
            return await self._download_image(
                urljoin(self._origin + "/", ref.lstrip("/"))
            )

        if self._origin:
            return await self._download_image(urljoin(self._origin + "/", ref))

        raise RuntimeError(f"不支持的图片 URL: {ref}")

    async def _download_image(self, url: str) -> Path:
        """下载图片"""
        client = await self._get_client()
        resp = await client.get(url)
        if resp.status_code != 200:
            raise RuntimeError(f"下载图片失败 HTTP {resp.status_code}")
        return await self._save_bytes(resp.content)

    async def _save_bytes(self, data: bytes) -> Path:
        """保存图片字节到文件"""
        mime = _guess_image_mime(data)
        ext = _guess_ext(mime)
        hash_part = hashlib.md5(data).hexdigest()[:8]
        filename = f"{int(time.time() * 1000)}_{hash_part}.{ext}"
        path = self.image_dir / filename
        await asyncio.to_thread(path.write_bytes, data)

        # 后台清理旧图片，避免阻塞主流程
        self._schedule_cleanup()

        return path

    def _schedule_cleanup(self) -> None:
        """调度后台清理任务（去重，避免任务堆积）"""
        if self._cleanup_task and not self._cleanup_task.done():
            return
        self._cleanup_task = asyncio.create_task(self._cleanup_background())

    async def _cleanup_background(self) -> None:
        """后台清理旧图片"""
        try:
            await asyncio.to_thread(self._cleanup)
        except Exception as e:
            logger.warning(f"[GrokDraw] 后台清理失败: {e}")

    def _cleanup(self) -> None:
        """清理超过限制的旧图片"""
        try:
            files = sorted(
                self.image_dir.glob("*.*"),
                key=lambda f: f.stat().st_mtime,
            )

            # 按数量清理
            if self.max_count > 0 and len(files) > self.max_count:
                for f in files[: len(files) - self.max_count]:
                    f.unlink(missing_ok=True)
                files = files[len(files) - self.max_count :]

            # 按大小清理
            if self.max_storage_mb > 0:
                max_bytes = self.max_storage_mb * 1024 * 1024
                total = sum(f.stat().st_size for f in files)
                while total > max_bytes and files:
                    oldest = files.pop(0)
                    total -= oldest.stat().st_size
                    oldest.unlink(missing_ok=True)

        except Exception as e:
            logger.warning(f"[GrokDraw] 清理图片失败: {e}")
