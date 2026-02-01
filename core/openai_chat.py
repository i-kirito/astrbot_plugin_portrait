import json
import re
from urllib.parse import urlparse

from curl_cffi.requests.exceptions import Timeout

from astrbot.api import logger

from .base import BaseProvider
from .data import ProviderConfig


def is_safe_url(url: str) -> bool:
    """检查 URL 是否安全（防止 SSRF）"""
    try:
        parsed = urlparse(url)
        # 只允许 https 协议
        if parsed.scheme != "https":
            logger.warning(f"[BIG BANANA] URL 不安全（非 https）: {url[:100]}")
            return False
        # 禁止访问内网地址
        hostname = parsed.hostname
        if not hostname:
            return False
        # 禁止 localhost 和私有 IP 段
        if hostname.lower() in ["localhost", "127.0.0.1", "::1"]:
            return False
        # 简单的私有IP检测（10.x, 172.16-31.x, 192.168.x）
        if hostname.startswith(("10.", "192.168.")):
            return False
        if hostname.startswith("172."):
            parts = hostname.split(".")
            if len(parts) >= 2 and parts[1].isdigit() and 16 <= int(parts[1]) <= 31:
                return False
        return True
    except Exception as e:
        logger.warning(f"[BIG BANANA] URL 解析失败: {url[:100]}, {e}")
        return False


class OpenAIChatProvider(BaseProvider):
    """OpenAI Chat 提供商"""

    api_type: str = "OpenAI_Chat"

    async def _call_api(
        self,
        provider_config: ProviderConfig,
        api_key: str,
        image_b64_list: list[tuple[str, str]],
        params: dict,
    ) -> tuple[list[tuple[str, str]] | None, int | None, str | None]:
        """发起 OpenAI 图片生成请求
        返回值: 元组(图片 base64 列表, 状态码, 人类可读的错误信息)
        """
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }
        # 构建请求上下文
        openai_context = self._build_openai_chat_context(
            provider_config.model, image_b64_list, params
        )
        try:
            # 发送请求
            response = await self.session.post(
                url=provider_config.api_url,
                headers=headers,
                json=openai_context,
                timeout=self.def_common_config.timeout,
                proxy=self.def_common_config.proxy,
            )
            # 响应反序列化
            result = response.json()
            if response.status_code == 200:
                b64_images = []
                images_url = []
                for item in result.get("choices", []):
                    # 检查 finish_reason 状态
                    finish_reason = item.get("finish_reason", "")
                    if finish_reason == "stop":
                        message = item.get("message", {})
                        content = message.get("content", "")

                        # 处理 message.images 字段（Gemini 图片生成格式）
                        images_list = message.get("images", [])
                        for img_item in images_list:
                            if isinstance(img_item, dict) and img_item.get("type") == "image_url":
                                img_url = img_item.get("image_url", {})
                                if isinstance(img_url, dict):
                                    url = img_url.get("url", "")
                                else:
                                    url = str(img_url)
                                if url.startswith("data:image/"):
                                    header, base64_data = url.split(",", 1)
                                    mime = header.split(";")[0].replace("data:", "")
                                    b64_images.append((mime, base64_data))
                                elif url and is_safe_url(url):
                                    images_url.append(url)
                                else:
                                    logger.warning(f"[BIG BANANA] 跳过不安全的 URL: {url[:100]}")

                        # 处理 content 可能是列表的情况（OpenAI Vision API 格式）
                        if isinstance(content, list):
                            # 从列表中提取文本或图片URL
                            for part in content:
                                if isinstance(part, dict):
                                    if part.get("type") == "text":
                                        content = part.get("text", "")
                                        break
                                    elif part.get("type") == "image_url":
                                        img_url = part.get("image_url", {})
                                        if isinstance(img_url, dict):
                                            url = img_url.get("url", "")
                                        else:
                                            url = str(img_url)
                                        if url.startswith("data:image/"):
                                            header, base64_data = url.split(",", 1)
                                            mime = header.split(";")[0].replace("data:", "")
                                            b64_images.append((mime, base64_data))
                                        elif url:
                                            images_url.append(url)
                            else:
                                content = ""  # 如果没有找到文本，设为空字符串
                        elif not isinstance(content, str):
                            content = str(content) if content else ""

                        # 从 markdown 格式的 content 中提取图片
                        if content:
                            match = re.search(r"!\[.*?\]\((.*?)\)", content)
                            if match:
                                img_src = match.group(1)
                                if img_src.startswith("data:image/"):  # base64
                                    header, base64_data = img_src.split(",", 1)
                                    mime = header.split(";")[0].replace("data:", "")
                                    b64_images.append((mime, base64_data))
                                elif is_safe_url(img_src):  # URL - 需要安全检查
                                    images_url.append(img_src)
                                else:
                                    logger.warning(f"[BIG BANANA] 跳过 markdown 中不安全的 URL: {img_src[:100]}")
                    else:
                        logger.warning(
                            f"[BIG BANANA] 图片生成失败, 响应内容: {response.text[:1024]}"
                        )
                        return None, 200, f"图片生成失败: {finish_reason}"
                # 最后再检查是否有图片数据
                if not images_url and not b64_images:
                    logger.warning(
                        f"[BIG BANANA] 请求成功，但未返回图片数据, 响应内容: {response.text[:1024]}"
                    )
                    return None, 200, "响应中未包含图片数据"
                # 下载图片并转换为 base64
                b64_images += await self.downloader.fetch_images(images_url)
                if not b64_images:
                    return None, 200, "图片下载失败"
                return b64_images, 200, None
            else:
                logger.error(
                    f"[BIG BANANA] 图片生成失败，状态码: {response.status_code}, 响应内容: {response.text[:1024]}"
                )
                return (
                    None,
                    response.status_code,
                    f"图片生成失败: 状态码 {response.status_code}",
                )
        except Timeout as e:
            logger.error(f"[BIG BANANA] 网络请求超时: {e}")
            return None, 408, "图片生成失败：响应超时"
        except json.JSONDecodeError as e:
            logger.error(
                f"[BIG BANANA] JSON反序列化错误: {e}，状态码：{response.status_code}，响应内容：{response.text[:1024]}"
            )
            return None, response.status_code, "图片生成失败：响应内容格式错误"
        except Exception as e:
            logger.error(f"[BIG BANANA] 请求错误: {e}")
            return None, None, "图片生成失败：程序错误"

    async def _call_stream_api(
        self,
        provider_config: ProviderConfig,
        api_key: str,
        image_b64_list: list[tuple[str, str]],
        params: dict,
    ) -> tuple[list[tuple[str, str]] | None, int | None, str | None]:
        """发起 OpenAI 图片生成流式请求
        返回值: 元组(图片 base64 列表, 状态码, 人类可读的错误信息)
        """
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }
        # 构建请求上下文
        openai_context = self._build_openai_chat_context(
            provider_config.model, image_b64_list, params
        )
        try:
            # 发送请求
            response = await self.session.post(
                url=provider_config.api_url,
                headers=headers,
                json=openai_context,
                proxy=self.def_common_config.proxy,
                stream=True,
            )
            # 处理流式响应
            streams = response.aiter_content(chunk_size=1024)
            # 读取完整内容
            data = b""
            async for chunk in streams:
                data += chunk
            result = data.decode("utf-8")
            if response.status_code == 200:
                b64_images = []
                images_url = []
                reasoning_content = ""
                for line in result.splitlines():
                    if line.startswith("data: "):
                        line_data = line[len("data: ") :].strip()
                        if line_data == "[DONE]":
                            break
                        try:
                            json_data = json.loads(line_data)
                            # 遍历 json_data，检查是否有图片
                            for item in json_data.get("choices", []):
                                delta = item.get("delta", {})
                                content = delta.get("content", "")

                                # 处理 delta.images 字段（Gemini 图片生成格式）
                                images_list = delta.get("images", [])
                                for img_item in images_list:
                                    if isinstance(img_item, dict) and img_item.get("type") == "image_url":
                                        img_url = img_item.get("image_url", {})
                                        if isinstance(img_url, dict):
                                            url = img_url.get("url", "")
                                        else:
                                            url = str(img_url)
                                        if url.startswith("data:image/"):
                                            header, base64_data = url.split(",", 1)
                                            mime = header.split(";")[0].replace("data:", "")
                                            b64_images.append((mime, base64_data))
                                        elif url:
                                            images_url.append(url)

                                # 处理 content 可能是列表的情况
                                if isinstance(content, list):
                                    for part in content:
                                        if isinstance(part, dict):
                                            if part.get("type") == "text":
                                                content = part.get("text", "")
                                                break
                                            elif part.get("type") == "image_url":
                                                img_url = part.get("image_url", {})
                                                if isinstance(img_url, dict):
                                                    url = img_url.get("url", "")
                                                else:
                                                    url = str(img_url)
                                                if url.startswith("data:image/"):
                                                    header, base64_data = url.split(",", 1)
                                                    mime = header.split(";")[0].replace("data:", "")
                                                    b64_images.append((mime, base64_data))
                                                elif url:
                                                    images_url.append(url)
                                    else:
                                        content = ""
                                elif not isinstance(content, str):
                                    content = str(content) if content else ""

                                # 从 markdown 格式提取图片
                                if content:
                                    match = re.search(r"!\[.*?\]\((.*?)\)", content)
                                    if match:
                                        img_src = match.group(1)
                                        if img_src.startswith("data:image/"):  # base64
                                            header, base64_data = img_src.split(",", 1)
                                            mime = header.split(";")[0].replace("data:", "")
                                            b64_images.append((mime, base64_data))
                                        else:  # URL
                                            images_url.append(img_src)
                                    else:  # 尝试查找失败的原因或者纯文本返回结果
                                        reasoning_content += delta.get(
                                            "reasoning_content", ""
                                        )
                        except json.JSONDecodeError:
                            continue
                if not images_url and not b64_images:
                    logger.warning(
                        f"[BIG BANANA] 请求成功，但未返回图片数据, 响应内容: {result[:1024]}"
                    )
                    return None, 200, reasoning_content or "响应中未包含图片数据"
                # 下载图片并转换为 base64（有时会出现连接被重置的错误，不知道什么原因，国外服务器也一样）
                b64_images += await self.downloader.fetch_images(images_url)
                if not b64_images:
                    return None, 200, "图片下载失败"
                return b64_images, 200, None
            else:
                logger.error(
                    f"[BIG BANANA] 图片生成失败，状态码: {response.status_code}, 响应内容: {result[:1024]}"
                )
                return None, response.status_code, "响应中未包含图片数据"
        except Timeout as e:
            logger.error(f"[BIG BANANA] 网络请求超时: {e}")
            return None, 408, "图片生成失败：响应超时"
        except Exception as e:
            logger.error(f"[BIG BANANA] 请求错误: {e}")
            return None, None, "图片生成失败：程序错误"

    def _build_openai_chat_context(
        self,
        model: str,
        image_b64_list: list[tuple[str, str]],
        params: dict,
    ) -> dict:
        images_content = []
        for mime, b64 in image_b64_list:
            images_content.append(
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}}
            )
        context = {
            "model": model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": params.get("prompt", "anything")},
                        *images_content,
                    ],
                }
            ],
            "stream": params.get("stream", False),
        }
        return context
