import asyncio

import aiohttp


# HTTP 会话单例
_http_session: aiohttp.ClientSession | None = None
_session_lock = asyncio.Lock()


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
