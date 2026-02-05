"""Gitee AI 文生图服务"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
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
        return "4096x4096"

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
        # size 和 resolution 都需要经过标准化处理
        final_size = (
            resolution_to_size(size or "")
            or resolution_to_size(resolution or "")
            or self.default_size
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

        # 后台清理，不阻塞返回
        asyncio.create_task(self._cleanup_background())

        return path

    async def _cleanup_background(self) -> None:
        """后台清理旧图片"""
        try:
            await self.imgr.cleanup_old_images()
        except Exception as e:
            logger.warning(f"[GiteeDrawService] 后台清理失败: {e}")
