import asyncio
import base64
import io
import mimetypes
from datetime import datetime
from pathlib import Path

import aiohttp

from astrbot.api import logger
from astrbot.core.message.components import At, Image, Reply
from astrbot.core.platform.astr_message_event import AstrMessageEvent

# 尝试导入 PIL 用于 GIF 处理
try:
    from PIL import Image as PILImage
except ImportError:
    PILImage = None


# HTTP 会话单例
_http_session: aiohttp.ClientSession | None = None
_session_lock = asyncio.Lock()


def get_key_index(current_index: int, item_len: int) -> int:
    """获取key索引"""
    return (current_index + 1) % item_len


def save_images(image_result: list[tuple[str, str]], path_dir: Path) -> list[tuple[str, Path]]:
    """保存图片到本地文件系统，返回 元组(文件名, 文件路径) 列表"""
    # 假设它支持返回多张图片
    saved_paths: list[tuple[str, Path]] = []
    for mime, b64 in image_result:
        if not b64:
            continue
        # 构建文件名
        now = datetime.now()
        current_time_str = (
            now.strftime("%Y%m%d%H%M%S") + f"{int(now.microsecond / 1000):03d}"
        )
        ext = mimetypes.guess_extension(mime) or ".jpg"
        file_name = f"banana_{current_time_str}{ext}"
        # 构建文件保存路径
        save_path = path_dir / file_name
        # 转换成bytes
        image_bytes = base64.b64decode(b64)
        # 保存到文件系统
        with open(save_path, "wb") as f:
            f.write(image_bytes)
        saved_paths.append((file_name, save_path))
        logger.info(f"[Portrait] 图片已保存到 {save_path}")
    return saved_paths


def read_file(path, allowed_dir=None) -> tuple[str | None, str | None]:
    """读取文件并返回 (mime_type, base64_data)

    Args:
        path: 文件路径
        allowed_dir: 允许的目录（用于路径穿越防护）
    """
    try:
        # 路径穿越防护
        resolved_path = Path(path).resolve()
        if allowed_dir is not None:
            allowed_resolved = Path(allowed_dir).resolve()
            if not str(resolved_path).startswith(str(allowed_resolved)):
                logger.warning(f"[Portrait] 路径穿越尝试被阻止: {path}")
                return None, None

        # 检查文件名是否包含危险字符
        filename = resolved_path.name
        if ".." in filename or filename.startswith("/"):
            logger.warning(f"[Portrait] 不安全的文件名: {filename}")
            return None, None

        with open(resolved_path, "rb") as f:
            file_data = f.read()
            mime_type, _ = mimetypes.guess_type(str(resolved_path))
            b64_data = base64.b64encode(file_data).decode("utf-8")
            return mime_type, b64_data
    except Exception as e:
        logger.error(f"[Portrait] 读取参考图片 {path} 失败: {e}")
        return None, None


def clear_cache(temp_dir: Path):
    """清理缓存文件，应当在图片发送完成后调用"""
    if not temp_dir.exists():
        logger.warning(f"[Portrait] 缓存目录 {temp_dir} 不存在")
        return
    for file in temp_dir.iterdir():
        try:
            if file.is_file():
                file.unlink()
                logger.debug(f"[Portrait] 已删除缓存文件: {file}")
        except Exception as e:
            logger.error(f"[Portrait] 删除缓存文件 {file} 失败: {e}")


def random_string(length: int) -> str:
    import random
    import string

    letters = string.ascii_letters + string.digits
    return "".join(random.choice(letters) for _ in range(length))


async def _get_session() -> aiohttp.ClientSession:
    """获取或创建 HTTP 会话（单例模式）"""
    global _http_session
    if _http_session is None or _http_session.closed:
        async with _session_lock:
            if _http_session is None or _http_session.closed:
                timeout = aiohttp.ClientTimeout(total=30, connect=10)
                connector = aiohttp.TCPConnector(limit=10, limit_per_host=5)
                _http_session = aiohttp.ClientSession(
                    timeout=timeout,
                    connector=connector,
                )
    return _http_session


async def close_session() -> None:
    """关闭 HTTP 会话"""
    global _http_session
    if _http_session and not _http_session.closed:
        await _http_session.close()
        _http_session = None


async def download_image(url: str, retries: int = 3) -> bytes | None:
    """下载图片，带重试机制

    Args:
        url: 图片 URL
        retries: 重试次数

    Returns:
        图片字节数据，失败返回 None
    """
    session = await _get_session()

    for i in range(retries):
        try:
            async with session.get(url) as resp:
                if resp.status == 200:
                    return await resp.read()
                logger.warning(f"[下载图片] HTTP {resp.status}: {url[:60]}...")
        except asyncio.TimeoutError:
            logger.warning(f"[下载图片] 超时 (第{i + 1}次): {url[:60]}...")
        except Exception as e:
            if i < retries - 1:
                await asyncio.sleep(1)
            else:
                logger.error(f"[下载图片] 失败: {url[:60]}..., 错误: {e}")
    return None


async def get_avatar(user_id: str) -> bytes | None:
    """获取 QQ 用户头像

    Args:
        user_id: QQ 号

    Returns:
        头像图片字节数据，失败返回 None
    """
    if not str(user_id).isdigit():
        return None

    avatar_url = f"https://q4.qlogo.cn/headimg_dl?dst_uin={user_id}&spec=640"
    raw = await download_image(avatar_url)

    if raw:
        return await _extract_first_frame(raw)
    return None


def _extract_first_frame_sync(raw: bytes) -> bytes:
    """提取 GIF 第一帧（同步方法）"""
    if PILImage is None:
        return raw
    try:
        img = PILImage.open(io.BytesIO(raw))
        if getattr(img, "is_animated", False):
            img.seek(0)
        img = img.convert("RGB")
        out = io.BytesIO()
        img.save(out, format="JPEG", quality=85)
        return out.getvalue()
    except Exception:
        return raw


async def _extract_first_frame(raw: bytes) -> bytes:
    """提取 GIF 第一帧（异步包装）"""
    return await asyncio.to_thread(_extract_first_frame_sync, raw)


async def get_images_from_event(
    event: AstrMessageEvent,
    include_avatar: bool = True,
) -> list[Image]:
    """从消息事件中提取图片组件列表

    图片来源：
    1. 回复/引用消息中的图片
    2. 当前消息中的图片
    3. @用户头像（有@时获取被@者头像）
    4. 发送者头像（无图片且无@时，作为兜底）

    Args:
        event: 消息事件
        include_avatar: 是否包含头像，默认 True

    Returns:
        Image 组件列表
    """
    image_segs: list[Image] = []
    chain = event.get_messages()

    logger.debug(
        f"[get_images] 消息链长度: {len(chain)}, 内容: {[type(seg).__name__ for seg in chain]}"
    )

    # 获取机器人自己的 ID（用于过滤@机器人）
    self_id = ""
    if hasattr(event, "get_self_id"):
        try:
            self_id = str(event.get_self_id()).strip()
        except Exception:
            pass

    # 收集所有有效的 @用户（排除@机器人自己和@all）
    at_user_ids: list[str] = []
    for seg in chain:
        if isinstance(seg, At) and hasattr(seg, "qq") and seg.qq != "all":
            uid = str(seg.qq)
            if uid != self_id and uid not in at_user_ids:
                at_user_ids.append(uid)

    # 1. 回复链中的图片
    for seg in chain:
        if isinstance(seg, Reply) and seg.chain:
            for chain_item in seg.chain:
                if isinstance(chain_item, Image):
                    image_segs.append(chain_item)
                    logger.debug("[get_images] 从回复中获取图片")

    # 2. 当前消息中的图片
    for seg in chain:
        if isinstance(seg, Image):
            image_segs.append(seg)
            logger.debug(
                f"[get_images] 从当前消息获取图片: url={getattr(seg, 'url', 'N/A')[:50] if getattr(seg, 'url', None) else 'N/A'}"
            )

    logger.debug(f"[get_images] 图片段数量: {len(image_segs)}, @用户: {at_user_ids}")

    # 3. 头像处理
    if include_avatar:
        if at_user_ids:
            for uid in at_user_ids:
                avatar_bytes = await get_avatar(uid)
                if avatar_bytes:
                    b64 = base64.b64encode(avatar_bytes).decode()
                    image_segs.append(Image.fromBase64(b64))
                    logger.debug(f"[get_images] 获取@用户头像成功: {uid}")
        elif not image_segs:
            sender_id = event.get_sender_id()
            if sender_id:
                avatar_bytes = await get_avatar(str(sender_id))
                if avatar_bytes:
                    b64 = base64.b64encode(avatar_bytes).decode()
                    image_segs.append(Image.fromBase64(b64))
                    logger.debug(f"[get_images] 获取发送者头像成功: {sender_id}")

    logger.debug(f"[get_images] 最终返回 {len(image_segs)} 个图片段")
    return image_segs


async def image_segs_to_bytes(image_segs: list[Image]) -> list[bytes]:
    """将 Image 组件列表转换为 bytes 列表"""
    out: list[bytes] = []
    for seg in image_segs:
        try:
            b64 = await seg.convert_to_base64()
            out.append(base64.b64decode(b64))
        except Exception as e:
            logger.warning(f"[图片] 转换失败，跳过: {e}")
    return out
