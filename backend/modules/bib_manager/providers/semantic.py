"""Semantic Scholar API 元数据提供者（补全层）。"""

import httpx

from backend.core.logger import get_logger
from backend.modules.bib_manager.models import DoiLookupResult
from backend.modules.bib_manager.providers.base import BaseMetadataProvider


class SemanticScholarProvider(BaseMetadataProvider):
    """Semantic Scholar API 提供者——补全层。

    API 端点：https://api.semanticscholar.org/graph/v1/paper/DOI:{doi}
    无认证开放 API，限流：100 req/5min（无 API key）。
    """

    name = "semantic_scholar"
    _API_URL = "https://api.semanticscholar.org/graph/v1/paper"

    def __init__(self):
        super().__init__()
        self._logger = get_logger("bib_manager.semantic_scholar")

    def _get_ua(self) -> str:
        from backend.core.config import get_config

        return get_config().http.user_agent

    async def lookup_by_doi(self, doi: str) -> DoiLookupResult | None:
        try:
            encoded_doi = doi.replace("/", "%2F")
            url = f"{self._API_URL}/DOI:{encoded_doi}"
            params = (
                "title,authors,year,journal,"
                "publicationVenue,externalIds,abstract,"
                "citationCount,openAccessPdf"
            )
            response = await self._get_http_client().get(
                url,
                params={"fields": params},
                headers={"User-Agent": self._get_ua()},
                timeout=30.0,
            )
            response.raise_for_status()

            data = response.json()
            if not data or data.get("paperId") is None:
                return None

            return self._parse_paper(doi, data)

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                self._logger.info(f"DOI not found in Semantic Scholar: {doi}")
            else:
                self._logger.warning(
                    f"Semantic Scholar API error: {e.response.status_code}"
                )
            return None
        except Exception as e:
            self._logger.warning(f"Semantic Scholar lookup failed: {e}")
            return None

    async def health_check(self) -> bool:
        try:
            response = await self._get_http_client().get(
                f"{self._API_URL}/DOI:10.1038%2F12345",
                params={"fields": "title"},
                headers={"User-Agent": self._get_ua()},
                timeout=10.0,
            )
            return response.status_code in (200, 404)
        except Exception:
            return False

    def _parse_paper(self, doi: str, data: dict) -> DoiLookupResult:
        title = data.get("title", "")

        authors = []
        for author in data.get("authors", []):
            name = author.get("name", "")
            if name:
                authors.append(name)

        year = str(data.get("year", ""))

        journal = ""
        venue = data.get("journal") or data.get("publicationVenue")
        if venue:
            journal = venue.get("name", "") if isinstance(venue, dict) else str(venue)

        volume = ""
        issue = ""
        pages = ""
        if isinstance(venue, dict):
            volume = str(venue.get("volume", ""))
            issue = str(venue.get("issue", ""))
            pages = venue.get("pages", "")

        publisher = ""
        if isinstance(venue, dict):
            publisher = venue.get("publisher", "")

        abstract = data.get("abstract", "")

        keywords = []

        url = f"https://doi.org/{doi}"
        oa_pdf = data.get("openAccessPdf", {})
        if oa_pdf and oa_pdf.get("url"):
            url = oa_pdf["url"]

        self._logger.info(f"Semantic Scholar lookup: {title[:50]}...")
        return DoiLookupResult(
            title=title,
            authors=authors,
            year=year,
            journal=journal,
            volume=volume,
            issue=issue,
            pages=pages,
            doi=doi,
            publisher=publisher,
            abstract=abstract,
            url=url,
            isbn="",
            issn="",
            keywords=keywords,
            data_source=self.name,
        )
