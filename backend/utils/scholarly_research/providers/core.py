"""CORE API 提供者 — 开放获取全文定位增强。"""

from typing import Any

import httpx

from backend.core.config import get_config
from backend.core.logger import get_logger

from ..base import BaseResearchProvider
from ..models import RelatedWorksResult, SearchScholarlyResult, WorkDetailResult

_API_BASE = "https://api.core.ac.uk/v3"


class COREProvider(BaseResearchProvider):
    """CORE 仅用于根据 DOI 查找开放获取全文 URL，不提供搜索或引文关系。"""

    name = "core"

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client
        self._logger = get_logger("scholarly_research.core")

    def _api_key(self) -> str:
        cfg = get_config().features.function_calling.scholarly_research
        return cfg.providers.core.api_key

    def _get_user_agent(self) -> str:
        return get_config().http.user_agent

    async def _request_json(
        self, path: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        cfg = get_config().features.function_calling.scholarly_research
        url = f"{_API_BASE}{path}"
        request_params = dict(params or {})
        headers: dict[str, str] = {"User-Agent": self._get_user_agent()}
        api_key = self._api_key()
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        response = await self._client.get(
            url, params=request_params, headers=headers, timeout=cfg.timeout_seconds
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise RuntimeError("CORE returned a non-object JSON response")
        return payload

    async def search(self, query: str, **filters: Any) -> SearchScholarlyResult:
        return SearchScholarlyResult(query=query, source=self.name)

    async def get_details(self, work_id: str) -> WorkDetailResult | None:
        return None

    async def find_related(
        self,
        seed_id: str | None = None,
        relation_type: str = "similar",
        **kwargs: Any,
    ) -> RelatedWorksResult:
        return RelatedWorksResult(seed_id=seed_id or "", relation_type=relation_type)

    async def find_oa_url(self, doi: str) -> str:
        """根据 DOI 查询 CORE 中的开放获取全文链接。"""
        try:
            payload = await self._request_json(
                f"/search/outputs",
                params={"doi": doi.strip(), "limit": 1},
            )
            results = payload.get("results", [])
            if results:
                return (
                    results[0].get("downloadUrl")
                    or results[0].get("sourceFulltextUrls", [None])[0]
                    or ""
                )
        except Exception as exc:
            self._logger.debug("CORE OA lookup failed for %s: %s", doi, exc)
        return ""
