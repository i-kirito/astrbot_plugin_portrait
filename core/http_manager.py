from aiohttp import ClientSession, ClientTimeout
from curl_cffi.requests import AsyncSession


class HttpManager:
    def __init__(self):
        self._aiohttp_session: ClientSession | None = None
        self._curl_session: AsyncSession | None = None

    def _get_aiohttp_session(self) -> ClientSession:
        """获取 ClientSession 对象
        使用Session时必须显式传递超时参数，这里未使用插件配置。
        """
        if self._aiohttp_session is None or self._aiohttp_session.closed:
            self._aiohttp_session = ClientSession(
                timeout=ClientTimeout(connect=15, total=30)
            )
        return self._aiohttp_session

    def _get_curl_session(self) -> AsyncSession:
        """获取 AsyncSession 对象
        使用Session时必须显式传递超时参数，这里未使用插件配置。
        """
        if self._curl_session is None or getattr(self._curl_session, "_closed", False):
            self._curl_session = AsyncSession(timeout=30)
        return self._curl_session

    async def close_session(self) -> None:
        """关闭客户端会话"""
        if self._aiohttp_session and not self._aiohttp_session.closed:
            await self._aiohttp_session.close()
            self._aiohttp_session = None

        if self._curl_session and not getattr(self._curl_session, "_closed", False):
            await self._curl_session.close()
            self._curl_session = None
