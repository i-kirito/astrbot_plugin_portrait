"""Gitee AI 文生图服务"""

from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import socket
import time
from pathlib import Path
from urllib.parse import urlparse

import aiohttp
from openai import AsyncOpenAI
from openai.types.images_response import ImagesResponse

from astrbot.api import logger

from .image_format import guess_image_mime_and_ext
from .image_manager import ImageManager

# 改图支持的任务类型
EDIT_TASK_TYPES = frozenset({"id", "style", "subject", "background", "element"})

# 不支持负面提示词的模型
MODELS_WITHOUT_NEGATIVE_PROMPT = frozenset({
    "z-image-turbo",
    "z-image-base",
    "flux.1-dev",
    "flux.1-schnell",
})

# 默认允许的 base_url 域名
DEFAULT_ALLOWED_HOSTS = frozenset({
    "ai.gitee.com",
    "api.gitee.com",
})


def _is_private_ip(host: str) -> bool:
    """检测是否为私网/回环/保留地址（支持域名解析）"""
    try:
        # 先尝试直接解析为 IP
        ip = ipaddress.ip_address(host)
        return ip.is_private or ip.is_loopback or ip.is_reserved or ip.is_link_local
    except ValueError:
        # 不是 IP 地址，尝试 DNS 解析
        try:
            resolved_ip = socket.gethostbyname(host)
            ip = ipaddress.ip_address(resolved_ip)
            return ip.is_private or ip.is_loopback or ip.is_reserved or ip.is_link_local
        except (socket.gaierror, ValueError):
            # DNS 解析失败，保守处理为安全（允许）
            return False


# Gitee AI 支持的所有尺寸
GITEE_SUPPORTED_SIZES = {
    # 正方形
    (256, 256), (512, 512), (1024, 1024), (2048, 2048),
    # 横版
    (1152, 896), (2048, 1536), (2048, 1360), (1024, 576), (2048, 1152),
    # 竖版
    (768, 1024), (1536, 2048), (1360, 2048), (576, 1024), (1152, 2048),
}


def _find_closest_size(width: int, height: int) -> str:
    """找到最接近的支持尺寸"""
    target_ratio = width / height
    target_area = width * height

    best_size = None
    best_score = float('inf')

    for w, h in GITEE_SUPPORTED_SIZES:
        # 计算比例差异（权重更高）和面积差异
        ratio = w / h
        ratio_diff = abs(ratio - target_ratio)
        area_diff = abs(w * h - target_area) / target_area  # 归一化

        # 综合评分：比例差异权重 2，面积差异权重 1
        score = ratio_diff * 2 + area_diff

        if score < best_score:
            best_score = score
            best_size = (w, h)

    return f"{best_size[0]}x{best_size[1]}" if best_size else "1024x1024"


def resolution_to_size(resolution: str) -> str | None:
    """将分辨率字符串转换为 Gitee 支持的尺寸

    Gitee AI 支持的尺寸:
    - 正方形: 256x256, 512x512, 1024x1024, 2048x2048
    - 横版: 1152x896, 2048x1536, 2048x1360, 1024x576, 2048x1152
    - 竖版: 768x1024, 1536x2048, 1360x2048, 576x1024, 1152x2048

    非标准尺寸会自动映射到最接近的支持尺寸
    """
    r = (resolution or "").strip().upper()
    if not r or r == "AUTO":
        return None

    # 标准分辨率关键词
    if r in {"1K", "1024"}:
        return "1024x1024"
    if r in {"2K", "2048"}:
        return "2048x2048"
    if r in {"4K", "4096"}:
        # Gitee 最大支持 2048x2048，4K 降级处理
        return "2048x2048"

    # 处理 WxH 格式
    if "X" in r:
        parts = r.split("X")
        if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
            w, h = int(parts[0]), int(parts[1])
            # 检查是否是支持的尺寸
            if (w, h) in GITEE_SUPPORTED_SIZES:
                return f"{w}x{h}"
            # 不支持则映射到最接近的尺寸
            return _find_closest_size(w, h)

    return None


class GiteeDrawService:
    """Gitee AI 文生图服务"""

    def __init__(
        self,
        data_dir: Path,
        api_keys: list[str],
        base_url: str = "https://ai.gitee.com/v1",
        model: str = "z-image-turbo",
        default_size: str = "1024x1024",
        num_inference_steps: int = 9,
        negative_prompt: str = "",
        timeout: int = 300,
        max_retries: int = 2,
        proxy: str | None = None,
        max_storage_mb: int = 500,
        max_count: int = 100,
        edit_model: str = "Qwen-Image-Edit-2511",
        edit_poll_interval: int = 5,
        edit_poll_timeout: int = 300,
    ):
        self.data_dir = Path(data_dir)
        self.api_keys = [k.strip() for k in api_keys if k.strip()]

        # 校验 base_url 防止 SSRF
        self.base_url = self._validate_base_url(base_url)

        self.model = model
        self.default_size = default_size
        self.num_inference_steps = num_inference_steps
        self.negative_prompt = negative_prompt
        self.timeout = timeout
        self.max_retries = max_retries
        self.proxy = proxy

        self._key_index = 0
        self._clients: dict[str, AsyncOpenAI] = {}
        self._cleanup_task: asyncio.Task | None = None
        self.imgr = ImageManager(
            data_dir,
            proxy=proxy,
            max_storage_mb=max_storage_mb,
            max_count=max_count,
        )

        # 改图配置 (异步任务模式)
        self.edit_model = edit_model
        self.edit_poll_interval = edit_poll_interval
        self.edit_poll_timeout = edit_poll_timeout
        self._edit_session: aiohttp.ClientSession | None = None
        self._edit_session_lock = asyncio.Lock()

    @staticmethod
    def _validate_base_url(url: str) -> str:
        """校验 base_url，阻断私网地址防止 SSRF"""
        url = (url or "").strip().rstrip("/")
        if not url:
            return "https://ai.gitee.com/v1"

        try:
            parsed = urlparse(url)
            host = (parsed.hostname or "").lower()

            # 阻断 localhost
            if host == "localhost":
                logger.warning("[GiteeDrawService] base_url 是 localhost，已阻断")
                return "https://ai.gitee.com/v1"

            # 白名单域名跳过私网检查
            if host not in DEFAULT_ALLOWED_HOSTS:
                # 使用 ipaddress 模块检测私网/回环地址
                if _is_private_ip(host):
                    logger.warning(f"[GiteeDrawService] base_url '{host}' 是私网地址，已阻断")
                    return "https://ai.gitee.com/v1"
                logger.info(f"[GiteeDrawService] 使用自定义 base_url: {url}")

            # 确保使用 HTTPS（仅警告，不强制）
            if parsed.scheme != "https":
                logger.warning("[GiteeDrawService] base_url 使用非 HTTPS 协议，建议使用 HTTPS")

            return url
        except Exception as e:
            logger.warning(f"[GiteeDrawService] 解析 base_url 失败: {e}，使用默认值")
            return "https://ai.gitee.com/v1"

    @property
    def enabled(self) -> bool:
        """是否已配置 API Key"""
        return bool(self.api_keys)

    async def close(self) -> None:
        """关闭资源"""
        if self._cleanup_task and not self._cleanup_task.done():
            self._cleanup_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._cleanup_task
        self._cleanup_task = None

        for client in self._clients.values():
            try:
                await client.close()
            except Exception:
                pass
        self._clients.clear()

        # 关闭改图 session
        if self._edit_session and not self._edit_session.closed:
            await self._edit_session.close()
            self._edit_session = None

        await self.imgr.close()

    def _next_key(self) -> str:
        if not self.api_keys:
            raise RuntimeError("未配置 Gitee AI API Key")
        # 确保索引在边界内（处理运行时 Key 被删除的情况）
        self._key_index = self._key_index % len(self.api_keys)
        key = self.api_keys[self._key_index]
        self._key_index = (self._key_index + 1) % len(self.api_keys)
        return key

    def _get_client(self, key: str) -> AsyncOpenAI:
        if key not in self._clients:
            self._clients[key] = AsyncOpenAI(
                base_url=self.base_url,
                api_key=key,
                timeout=self.timeout,
                max_retries=self.max_retries,
            )
        return self._clients[key]

    async def generate(
        self,
        prompt: str,
        *,
        size: str | None = None,
        resolution: str | None = None,
        model: str | None = None,
        num_inference_steps: int | None = None,
        negative_prompt: str | None = None,
    ) -> Path:
        """生成图片

        Args:
            prompt: 提示词
            size: 尺寸 (如 "1024x1024")
            resolution: 分辨率 (如 "1K", "2K", "4K")
            model: 模型名称
            num_inference_steps: 推理步数
            negative_prompt: 负面提示词

        Returns:
            生成的图片路径
        """
        if not self.enabled:
            raise RuntimeError("未配置 Gitee AI API Key")

        key = self._next_key()
        client = self._get_client(key)

        final_model = model or self.model
        # size 和 resolution 都需要经过标准化处理
        final_size = (
            resolution_to_size(size or "")
            or resolution_to_size(resolution or "")
            or self.default_size
        )

        # 验证 final_size 是否为 Gitee 支持的格式
        if final_size and "x" in final_size.lower():
            try:
                w, h = map(int, final_size.lower().split("x"))
                if (w, h) not in GITEE_SUPPORTED_SIZES:
                    # 不支持的尺寸，映射到最接近的
                    final_size = _find_closest_size(w, h)
                    logger.debug(f"[GiteeDrawService] 尺寸 {w}x{h} 不支持，映射到 {final_size}")
            except (ValueError, AttributeError):
                final_size = "1024x1024"
                logger.warning(f"[GiteeDrawService] 无效的尺寸格式，使用默认 1024x1024")

        final_steps = num_inference_steps or self.num_inference_steps
        final_negative = negative_prompt or self.negative_prompt

        # 构建 extra_body
        extra_body: dict = {}
        if final_steps:
            extra_body["num_inference_steps"] = final_steps
        if final_negative and final_model.lower() not in MODELS_WITHOUT_NEGATIVE_PROMPT:
            extra_body["negative_prompt"] = final_negative

        kwargs: dict = {
            "model": final_model,
            "prompt": prompt,
            "size": final_size,
        }
        if extra_body:
            kwargs["extra_body"] = extra_body

        t0 = time.time()
        try:
            resp: ImagesResponse = await client.images.generate(**kwargs)
        except Exception as e:
            logger.error(
                f"[GiteeDrawService] API 调用失败，耗时: {time.time() - t0:.2f}s: {e}"
            )
            raise

        logger.info(f"[GiteeDrawService] API 响应耗时: {time.time() - t0:.2f}s")

        if not resp.data:
            raise RuntimeError("Gitee AI 未返回图片数据")

        img = resp.data[0]
        if getattr(img, "url", None):
            path = await self.imgr.download_image(img.url, prompt=prompt)
        elif getattr(img, "b64_json", None):
            path = await self.imgr.save_base64_image(img.b64_json, prompt=prompt)
        else:
            raise RuntimeError("Gitee AI 返回数据不包含图片")

        # 后台清理，不阻塞返回（去重，避免任务堆积）
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
            await self.imgr.cleanup_old_images()
        except Exception as e:
            logger.warning(f"[GiteeDrawService] 后台清理失败: {e}")

    # ==================== 异步改图 (Qwen-Image-Edit-2511) ====================

    async def _get_edit_session(self) -> aiohttp.ClientSession:
        """获取或创建改图用的 HTTP Session (线程安全)"""
        if self._edit_session is None or self._edit_session.closed:
            async with self._edit_session_lock:
                if self._edit_session is None or self._edit_session.closed:
                    connector = aiohttp.TCPConnector(
                        limit=10,
                        limit_per_host=5,
                        ttl_dns_cache=300,
                        enable_cleanup_closed=True,
                    )
                    timeout = aiohttp.ClientTimeout(
                        total=self.edit_poll_timeout + 60,
                        connect=30,
                    )
                    self._edit_session = aiohttp.ClientSession(
                        connector=connector,
                        timeout=timeout,
                    )
        return self._edit_session

    async def edit(
        self,
        prompt: str,
        images: list[bytes],
        task_types: tuple[str, ...] = ("id",),
    ) -> Path:
        """执行异步改图

        Args:
            prompt: 提示词
            images: 图片字节列表
            task_types: 任务类型 (id/style/subject/background/element)

        Returns:
            生成图片的本地路径
        """
        if not self.enabled:
            raise RuntimeError("未配置 Gitee AI API Key")
        if not images:
            raise ValueError("至少需要一张图片")

        api_key = self._next_key()
        t_start = time.perf_counter()

        logger.info(
            f"[GiteeDrawService] 开始改图: model={self.edit_model}, "
            f"task_types={list(task_types)}, images={len(images)}"
        )

        # 创建异步任务
        task_id = await self._create_edit_task(prompt, images, task_types, api_key)
        t_create = time.perf_counter()
        logger.debug(
            f"[GiteeDrawService] 改图任务创建成功: {task_id}, 耗时: {t_create - t_start:.2f}s"
        )

        # 轮询结果
        file_url = await self._poll_edit_task(task_id, api_key)
        t_poll = time.perf_counter()
        logger.debug(f"[GiteeDrawService] 改图任务完成, 轮询耗时: {t_poll - t_create:.2f}s")

        # 下载图片
        result_path = await self.imgr.download_image(file_url, prompt=prompt)
        t_end = time.perf_counter()

        logger.info(
            f"[GiteeDrawService] 改图完成: 总耗时={t_end - t_start:.2f}s, "
            f"创建={t_create - t_start:.2f}s, 轮询={t_poll - t_create:.2f}s, "
            f"下载={t_end - t_poll:.2f}s"
        )

        self._schedule_cleanup()
        return result_path

    async def _create_edit_task(
        self,
        prompt: str,
        images: list[bytes],
        task_types: tuple[str, ...],
        api_key: str,
    ) -> str:
        """创建异步改图任务"""
        session = await self._get_edit_session()

        data = aiohttp.FormData()
        data.add_field("prompt", prompt)
        data.add_field("model", self.edit_model)
        data.add_field("num_inference_steps", str(self.num_inference_steps))
        data.add_field("guidance_scale", "1.0")

        for t in task_types:
            if t in EDIT_TASK_TYPES:
                data.add_field("task_types", t)

        for i, img in enumerate(images):
            mime, ext = guess_image_mime_and_ext(img)
            data.add_field(
                "image",
                img,
                filename=f"image_{i}.{ext}",
                content_type=mime,
            )

        try:
            async with session.post(
                f"{self.base_url}/async/images/edits",
                headers={"Authorization": f"Bearer {api_key}"},
                data=data,
            ) as resp:
                result = await resp.json()

                if resp.status != 200:
                    error_msg = result.get("message", str(result))
                    logger.error(f"[GiteeDrawService] 创建改图任务失败 ({resp.status}): {error_msg}")
                    raise RuntimeError(f"Gitee 创建改图任务失败: {error_msg}")

                task_id = result.get("task_id")
                if not task_id:
                    logger.error(f"[GiteeDrawService] 响应未包含 task_id: {result}")
                    raise RuntimeError("Gitee 未返回 task_id")

                return task_id

        except aiohttp.ClientError as e:
            logger.error(f"[GiteeDrawService] 改图网络错误: {e}")
            raise RuntimeError(f"Gitee 改图网络错误: {e}")

    async def _poll_edit_task(self, task_id: str, api_key: str) -> str:
        """轮询改图任务状态直到完成"""
        session = await self._get_edit_session()
        url = f"{self.base_url}/task/{task_id}"
        max_rounds = self.edit_poll_timeout // self.edit_poll_interval

        for i in range(max_rounds):
            try:
                async with session.get(
                    url,
                    headers={"Authorization": f"Bearer {api_key}"},
                ) as resp:
                    result = await resp.json()
                    status = result.get("status")

                    if status == "success":
                        file_url = result.get("output", {}).get("file_url")
                        if not file_url:
                            logger.error(f"[GiteeDrawService] 任务成功但无 file_url: {result}")
                            raise RuntimeError("Gitee 任务成功但未返回 file_url")
                        return file_url

                    if status in {"failed", "cancelled"}:
                        error_msg = result.get("message", status)
                        logger.error(f"[GiteeDrawService] 改图任务失败: {error_msg}")
                        raise RuntimeError(f"Gitee 改图任务失败: {error_msg}")

                    # 每 5 轮输出一次日志，减少日志噪音
                    if (i + 1) % 5 == 0:
                        logger.debug(f"[GiteeDrawService] 轮询第{i + 1}轮, 状态: {status}")

            except aiohttp.ClientError as e:
                logger.warning(f"[GiteeDrawService] 轮询网络错误 (第{i + 1}轮): {e}")

            await asyncio.sleep(self.edit_poll_interval)

        logger.error(f"[GiteeDrawService] 改图任务超时 (>{self.edit_poll_timeout}s)")
        raise TimeoutError(f"Gitee 改图任务超时 (>{self.edit_poll_timeout}s)")
