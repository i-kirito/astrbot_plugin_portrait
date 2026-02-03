"""Gemini AI 文生图服务 - 支持原生接口和 OpenAI 兼容接口"""

from __future__ import annotations

import base64
from pathlib import Path
from urllib.parse import urlparse

import aiohttp
from astrbot.api import logger

from .image_manager import ImageManager

# 允许的 base_url 域名白名单（防止 SSRF）
ALLOWED_GEMINI_HOSTS = frozenset({
    "generativelanguage.googleapis.com",
    "aiplatform.googleapis.com",
    "api.openai.com",  # OpenAI 兼容接口
})


class GeminiDrawService:
    """Google Gemini AI 文生图服务

    默认使用原生 Gemini 接口 (/v1beta/models)，失败时回退到 OpenAI 兼容接口 (/v1/chat/completions)
    - 2K/4K 分辨率仅原生接口支持（gemini-3 系列）
    """

    def __init__(
        self,
        data_dir: Path,
        api_key: str = "",
        base_url: str = "https://generativelanguage.googleapis.com",
        model: str = "gemini-2.0-flash-exp-image-generation",
        image_size: str = "1K",
        timeout: int = 120,
        proxy: str | None = None,
        max_storage_mb: int = 500,
        max_count: int = 100,
    ):
        self.data_dir = Path(data_dir)
        self.api_key = api_key.strip() if api_key else ""
        self.model = model.strip() if model else "gemini-2.0-flash-exp-image-generation"
        self.image_size = image_size.upper() if image_size else "1K"
        self.timeout = timeout
        self.proxy = proxy

        # 处理 base_url，校验并移除末尾斜杠和路径
        self.base_url = self._validate_base_url(base_url)

        # 图片管理器
        self.imgr = ImageManager(
            data_dir=self.data_dir,
            proxy=proxy,
            max_storage_mb=max_storage_mb,
            max_count=max_count,
        )

    @staticmethod
    def _validate_base_url(url: str) -> str:
        """校验 base_url，只允许白名单域名（防止 SSRF）"""
        url = (url or "").strip().rstrip("/")
        if not url:
            return "https://generativelanguage.googleapis.com"

        try:
            parsed = urlparse(url)
            host = parsed.netloc.lower()

            # 移除可能存在的路径后缀
            path = parsed.path
            for suffix in ["/v1beta/models", "/v1beta", "/v1/chat/completions", "/v1"]:
                if path.endswith(suffix):
                    path = path[:-len(suffix)]
                    break

            # 检查是否在白名单中
            if host not in ALLOWED_GEMINI_HOSTS:
                logger.warning(
                    f"[GeminiDrawService] base_url '{url}' 不在允许列表中，使用默认值"
                )
                return "https://generativelanguage.googleapis.com"

            # 确保使用 HTTPS
            scheme = "https"
            clean_url = f"{scheme}://{host}{path}".rstrip("/")
            return clean_url

        except Exception as e:
            logger.warning(f"[GeminiDrawService] 解析 base_url 失败: {e}，使用默认值")
            return "https://generativelanguage.googleapis.com"

    @property
    def enabled(self) -> bool:
        """检查服务是否可用"""
        return bool(self.api_key)

    @property
    def use_native_api(self) -> bool:
        """判断是否使用原生 API（默认始终使用原生接口）"""
        return True

    async def generate(self, prompt: str) -> Path:
        """生成图片

        Args:
            prompt: 图片描述提示词

        Returns:
            生成的图片路径

        Raises:
            Exception: 生成失败时抛出异常
        """
        if not self.enabled:
            raise ValueError("Gemini AI 未配置 API Key")

        logger.info(f"[Gemini] 开始生成图片 (size={self.image_size}): {prompt[:50]}...")

        # 默认使用原生接口，失败时回退到 OpenAI 兼容接口
        try:
            image_bytes = await self._generate_native(prompt)
        except Exception as e:
            logger.warning(f"[Gemini] 原生接口失败: {e}，尝试 OpenAI 兼容接口")
            image_bytes = await self._generate_openai_compatible(prompt)

        # 保存图片
        path = await self.imgr.save_image_bytes(image_bytes, prompt=prompt)
        logger.info(f"[Gemini] 图片已保存: {path}")

        # 保存后触发清理
        await self.imgr.cleanup_old_images()

        return path

    async def _generate_native(self, prompt: str) -> bytes:
        """使用原生 Gemini API 生成图片 (支持 2K/4K)"""
        url = f"{self.base_url}/v1beta/models/{self.model}:generateContent"

        headers = {
            "Content-Type": "application/json",
            "x-goog-api-key": self.api_key,
        }

        # 构建请求体
        payload = {
            "contents": [
                {"parts": [{"text": prompt}]}
            ],
            "generationConfig": {
                "responseModalities": ["IMAGE"],
            },
            "safetySettings": [
                {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "OFF"},
                {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "OFF"},
                {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "OFF"},
                {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "OFF"},
            ],
        }

        # 仅 gemini-3 系列支持 imageSize 参数
        if "gemini-3" in self.model.lower():
            payload["generationConfig"]["imageConfig"] = {"imageSize": self.image_size}

        logger.debug(f"[Gemini Native] URL: {url}")

        timeout = aiohttp.ClientTimeout(total=self.timeout)

        async with aiohttp.ClientSession(timeout=timeout) as session:
            try:
                async with session.post(
                    url,
                    json=payload,
                    proxy=self.proxy if self.proxy else None,
                    headers=headers,
                ) as resp:
                    if resp.status != 200:
                        error_text = await resp.text()
                        logger.error(f"[Gemini Native] API 错误: {resp.status} - {error_text}")
                        raise Exception(f"Gemini API 错误: {resp.status}")

                    data = await resp.json()

            except aiohttp.ClientError as e:
                logger.error(f"[Gemini Native] 请求失败: {e}")
                raise Exception(f"Gemini 请求失败: {str(e)}")

        # 解析响应
        return self._parse_native_response(data)

    async def _generate_openai_compatible(self, prompt: str) -> bytes:
        """使用 OpenAI 兼容接口生成图片（作为原生接口的回退）"""
        url = f"{self.base_url}/v1/chat/completions"

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

        # OpenAI 格式的请求体
        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt}
                    ]
                }
            ],
            "max_tokens": 4096,
        }

        logger.debug(f"[Gemini OpenAI] URL: {url}")

        timeout = aiohttp.ClientTimeout(total=self.timeout)

        async with aiohttp.ClientSession(timeout=timeout) as session:
            try:
                async with session.post(
                    url,
                    json=payload,
                    proxy=self.proxy if self.proxy else None,
                    headers=headers,
                ) as resp:
                    if resp.status != 200:
                        error_text = await resp.text()
                        logger.error(f"[Gemini OpenAI] API 错误: {resp.status} - {error_text}")
                        raise Exception(f"Gemini OpenAI 兼容接口错误: {resp.status}")

                    data = await resp.json()

            except aiohttp.ClientError as e:
                logger.error(f"[Gemini OpenAI] 请求失败: {e}")
                raise Exception(f"Gemini OpenAI 兼容接口请求失败: {str(e)}")

        # 解析 OpenAI 格式响应
        return self._parse_openai_response(data)

    def _parse_native_response(self, data: dict) -> bytes:
        """解析原生 Gemini API 响应"""
        try:
            candidates = data.get("candidates", [])
            if not candidates:
                # 检查 promptFeedback 拦截
                feedback = data.get("promptFeedback", {})
                if feedback.get("blockReason"):
                    raise Exception(f"内容被拦截: {feedback.get('blockReason')}")
                raise Exception("Gemini 未返回有效内容")

            content = candidates[0].get("content", {})
            parts = content.get("parts", [])

            # 检查 finishReason
            finish_reason = candidates[0].get("finishReason", "")
            if finish_reason and finish_reason != "STOP":
                finish_msg = candidates[0].get("finishMessage", "")
                if finish_msg:
                    raise Exception(f"生成失败: {finish_msg[:100]}")
                raise Exception(f"生成失败: {finish_reason}")

            for part in parts:
                if "inlineData" in part:
                    inline_data = part["inlineData"]
                    if inline_data.get("mimeType", "").startswith("image/"):
                        image_b64 = inline_data.get("data")
                        if image_b64:
                            return base64.b64decode(image_b64)

            raise Exception("Gemini 响应中未找到图片数据")

        except (KeyError, IndexError) as e:
            logger.error(f"[Gemini] 解析响应失败: {e}, data={data}")
            raise Exception(f"Gemini 响应解析失败: {str(e)}")

    def _parse_openai_response(self, data: dict) -> bytes:
        """解析 OpenAI 兼容格式响应"""
        try:
            choices = data.get("choices", [])
            if not choices:
                raise Exception("OpenAI 响应未返回有效内容")

            message = choices[0].get("message", {})
            content = message.get("content", [])

            # content 可能是字符串或列表
            if isinstance(content, str):
                # 尝试从字符串中提取 base64 图片
                raise Exception("OpenAI 响应格式不包含图片数据")

            for item in content:
                if isinstance(item, dict):
                    # 检查 image_url 格式
                    if item.get("type") == "image_url":
                        url = item.get("image_url", {}).get("url", "")
                        if url.startswith("data:image"):
                            # data:image/png;base64,xxx
                            _, b64_data = url.split(",", 1)
                            return base64.b64decode(b64_data)
                    # 检查 inlineData 格式 (一些代理服务使用)
                    if "inlineData" in item:
                        inline_data = item["inlineData"]
                        if inline_data.get("data"):
                            return base64.b64decode(inline_data["data"])

            raise Exception("OpenAI 响应中未找到图片数据")

        except (KeyError, IndexError) as e:
            logger.error(f"[Gemini OpenAI] 解析响应失败: {e}, data={data}")
            raise Exception(f"响应解析失败: {str(e)}")

    async def close(self):
        """关闭服务（释放资源）"""
        pass
