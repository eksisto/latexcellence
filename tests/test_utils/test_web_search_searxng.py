"""SearXNG 搜索引擎单元测试。"""

import httpx
import pytest

from backend.utils.web_search.engines.searxng import SearXNGEngine


@pytest.mark.asyncio
async def test_searxng_connect_error_raises_instead_of_empty():
    """连接失败必须作为错误抛出：返回空结果会被上层当作"搜索无结果"，
    主引擎断网时不触发降级、熔断计数也不累计。"""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    engine = SearXNGEngine()
    try:
        with pytest.raises(RuntimeError, match="connection failed"):
            await engine._do_search(client, "http://searx.example", "latex", 5)
    finally:
        await client.aclose()
