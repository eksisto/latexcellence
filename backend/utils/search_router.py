"""
通用搜索 API 路由。

端点：
    POST /api/search/ddg-challenge  — 提交 DDG 验证码解答
"""

from fastapi import APIRouter

from backend.utils.web_search import DDGChallengeSubmitRequest, DuckDuckGoHtmlEngine

router = APIRouter(prefix="/api/search", tags=["search"])


@router.post("/ddg-challenge")
async def submit_ddg_challenge(request: DDGChallengeSubmitRequest):
    """
    提交 DDG 验证码解答。

    前端在用户选择正确的鸭子图片后调用此接口，
    后端将解答提交到 DDG 并返回结果。
    """
    result = await DuckDuckGoHtmlEngine.submit_challenge(
        request.challenge_id, request.selected_hashes
    )
    return result
