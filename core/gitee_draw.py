"""Gitee AI 文生图服务"""

from __future__ import annotations

import time
from pathlib import Path
from urllib.parse import urlparse

from openai import AsyncOpenAI
from openai.types.images_response import ImagesResponse

from astrbot.api import logger

from .image_manager import ImageManager

# 不支持负面提示词的模型
MODELS_WITHOUT_NEGATIVE_PROMPT = frozenset({
    "z-image-turbo",
    "z-image-base",
    "flux.1-dev",
    "flux.1-schnell",
})

# 允许的 base_url 域名白名单（防止 SSRF）
ALLOWED_BASE_URLS = frozenset({
    "ai.gitee.com",
    "api.gitee.com",
})


def resolution_to_size(resolution: str) -> str | None:
    """将分辨率字符串转换为尺寸"""
    r = (resolution or "").strip().upper()
    if not r or r == "AUTO":
        return None
    if r in {"1K", "1024"}:
        return "1024x1024"
    if r in {"2K", "2048"}:
        return "2048x2048"
    if r in {"4K", "4096"}:
        return "4096x4096"
    # 检查是否是 WxH 格式
    if "X" in r and r.replace("X", "").replace("x", "").isdigit():
        return r.lower()
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
        self.imgr = ImageManager(
            data_dir,
            proxy=proxy,
            max_storage_mb=max_storage_mb,
            max_count=max_count,
        )

    @staticmethod
    def _validate_base_url(url: str) -> str:
        """校验 base_url，只允许白名单域名"""
        url = (url or "").strip().rstrip("/")
        if not url:
            return "https://ai.gitee.com/v1"

        try:
            parsed = urlparse(url)
            host = parsed.netloc.lower()

            # 检查是否在白名单中
            if host not in ALLOWED_BASE_URLS:
                logger.warning(
                    f"[GiteeDrawService] base_url '{url}' 不在允许列表中，使用默认值"
                )
                return "https://ai.gitee.com/v1"

            # 确保使用 HTTPS
            if parsed.scheme != "https":
                url = url.replace(parsed.scheme + "://", "https://", 1)

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
        for client in self._clients.values():
            try:
                await client.close()
            except Exception:
                pass
        self._clients.clear()
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
        final_size = (
            size or resolution_to_size(resolution or "") or self.default_size
        )
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

        # 保存后触发清理
        await self.imgr.cleanup_old_images()

        return path
