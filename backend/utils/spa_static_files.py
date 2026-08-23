"""
SPA 静态文件托管（history 路由回退）。

前端使用 React Router 的 BrowserRouter（history 模式），`/formula-ocr` 等
客户端路由在 static/ 下没有对应物理文件；`StaticFiles(html=True)` 只能命中
物理文件或目录索引，刷新 / 直达这类路径会抛 404，经 FastAPI 默认异常处理器
渲染成 API 风格的 `{"detail":"Not Found"}`。

本模块提供带 SPA 回退的静态托管：仅当请求是「期望 HTML 的页面导航」且路径
未命中任何物理文件时，回退返回 index.html，由前端路由接管渲染。脚本、样式、
图片等静态资源缺失以及 /api/* 未命中时保持原样 404，避免把 HTML 错当 JS/CSS
加载或掩盖接口路径错误。

用法（生产模式挂载，见 main.py）：

    app.mount("/", SPAStaticFiles(directory="static", html=True), name="static")
"""

from starlette.datastructures import Headers
from starlette.exceptions import HTTPException
from starlette.responses import Response
from starlette.staticfiles import StaticFiles
from starlette.types import Scope

# 挂载点为 "/" 时，StaticFiles 传给 get_response 的 path 不含前导 "/"，
# 因此 API 前缀按 "api/" 比较（而非 "/api/"）
_API_PREFIX = "api/"

# 浏览器页面导航的 Accept 必然包含 text/html；脚本（*/*）、样式（text/css）、
# 图片（image/*）等资源请求不含，以此区分「找页面」和「找资源」
_HTML_ACCEPT = "text/html"


class SPAStaticFiles(StaticFiles):
    """
    带 SPA 回退的静态文件服务。

    行为规则：
    - 物理文件 / 目录索引命中 → 照常返回（含 304 协商缓存与目录重定向）；
    - 未命中且为期望 HTML 的 GET 页面导航、且不以 api/ 开头 → 回退 index.html；
    - 其余情况（资源缺失、API 未命中）→ 保持 404，由 FastAPI 返回 JSON 错误体。
    """

    async def get_response(self, path: str, scope: Scope) -> Response:
        try:
            return await super().get_response(path, scope)
        except HTTPException as exc:
            if exc.status_code == 404 and self._should_fallback(path, scope):
                # 复用父类的完整响应逻辑（MIME、ETag、Range 等），等价于
                # 直接请求 "/index.html"
                return await super().get_response("index.html", scope)
            raise

    @staticmethod
    def _should_fallback(path: str, scope: Scope) -> bool:
        """判断一次 404 是否应当回退到 index.html。"""
        headers = Headers(scope=scope)
        if _HTML_ACCEPT not in headers.get("accept", "").lower():
            # 非 HTML 导航（脚本/样式/图片/fetch）：资源缺失保持 404
            return False
        # API 前缀未命中时保持 API 风格 404，不回退 HTML
        return not path.lstrip("/").lower().startswith(_API_PREFIX)
