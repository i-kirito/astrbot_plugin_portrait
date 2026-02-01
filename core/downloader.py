import base64
import ipaddress
import socket
from io import BytesIO
from urllib.parse import urlparse

from curl_cffi import AsyncSession
from curl_cffi.requests.exceptions import (
    CertificateVerifyError,
    SSLError,
    Timeout,
)
from PIL import Image

from astrbot.api import logger

from .data import SUPPORTED_FILE_FORMATS, CommonConfig


def is_safe_url(url: str) -> bool:
    """检查 URL 是否安全（防止 SSRF 攻击）"""
    try:
        parsed = urlparse(url)
        # 只允许 http 和 https
        if parsed.scheme not in ("http", "https"):
            logger.warning(f"[BIG BANANA] 不安全的 URL scheme: {parsed.scheme}")
            return False

        hostname = parsed.hostname
        if not hostname:
            return False

        # 解析域名获取 IP
        try:
            ip = socket.gethostbyname(hostname)
            ip_obj = ipaddress.ip_address(ip)

            # 拒绝私有/保留 IP
            if ip_obj.is_private or ip_obj.is_loopback or ip_obj.is_reserved:
                logger.warning(f"[BIG BANANA] URL 解析到私有/保留 IP: {ip}")
                return False
        except socket.gaierror:
            # 域名解析失败，允许继续（可能是临时 DNS 问题）
            pass

        return True
    except Exception as e:
        logger.warning(f"[BIG BANANA] URL 安全检查失败: {e}")
        return False


class Downloader:
    def __init__(self, session: AsyncSession, common_config: CommonConfig):
        self.session = session
        self.def_common_config = common_config

    async def fetch_image(self, url: str) -> tuple[str, str] | None:
        """下载单张图片并转换为 (mime, base64)"""
        # 重试逻辑
        for _ in range(3):
            content = await self._download_image(url)
            if content is not None:
                return content

    async def fetch_images(self, image_urls: list[str]) -> list[tuple[str, str]]:
        """下载多张图片并转换为 (mime, base64) 列表"""
        image_b64_list = []
        for url in image_urls:
            # 重试逻辑
            for _ in range(3):
                content = await self._download_image(url)
                if content is not None:
                    image_b64_list.append(content)
                    break  # 成功就跳出重试
        return image_b64_list

    @staticmethod
    def _handle_image(image_bytes: bytes) -> tuple[str, str] | None:
        if len(image_bytes) > 36 * 1024 * 1024:
            logger.warning("[BIG BANANA] 图片超过 36MB，跳过处理")
            return None
        try:
            with Image.open(BytesIO(image_bytes)) as img:
                fmt = (img.format or "").lower()
                if fmt not in SUPPORTED_FILE_FORMATS:
                    logger.warning(f"[BIG BANANA] 不支持的图片格式: {fmt}")
                    return None
                # 如果不是 GIF，直接返回原图
                if fmt != "gif":
                    if fmt == "jpg":
                        mime = "image/jpeg"
                    else:
                        mime = f"image/{fmt}"
                    b64 = base64.b64encode(image_bytes).decode("utf-8")
                    return (mime, b64)
                # 处理 GIF
                buf = BytesIO()
                # 取第一帧
                img.seek(0)
                img = img.convert("RGBA")
                img.save(buf, format="PNG")
                b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
                return ("image/png", b64)
        except Exception as e:
            logger.warning(f"[BIG BANANA] GIF 处理失败: {e}")
            return None

    async def _download_image(self, url: str) -> tuple[str, str] | None:
        # SSRF 防护：验证 URL 安全性
        if not is_safe_url(url):
            logger.warning(f"[BIG BANANA] 拒绝不安全的 URL: {url}")
            return None

        try:
            response = await self.session.get(
                url,
                proxy=self.def_common_config.proxy,
                timeout=30,
            )
            if response.status_code != 200 or not response.content:
                logger.warning(
                    f"[BIG BANANA] 图片下载失败，状态码: {response.status_code}"
                )
                return None
            content = Downloader._handle_image(response.content)
            return content
        except (SSLError, CertificateVerifyError):
            # 关闭SSL验证
            response = await self.session.get(url, timeout=30, verify=False)
            if response.status_code != 200 or not response.content:
                logger.warning(
                    f"[BIG BANANA] 图片下载失败，状态码: {response.status_code}"
                )
                return None
            content = Downloader._handle_image(response.content)
            return content
        except Timeout as e:
            logger.error(f"[BIG BANANA] 网络请求超时: {url}，错误信息：{e}")
            return None
        except Exception as e:
            logger.error(f"[BIG BANANA] 下载图片失败: {url}，错误信息：{e}")
            return None
