"""Crossref API 元数据提供者（主索引）。"""

import re

import httpx

from backend.core.logger import get_logger
from backend.modules.bib_manager.models import DoiLookupResult
from backend.modules.bib_manager.providers.base import BaseMetadataProvider


class CrossrefProvider(BaseMetadataProvider):
    """Crossref API 提供者——作为 DOI 查询的主索引。

    API 端点：https://api.crossref.org/works/{doi}
    限流：建议使用 User-Agent 标识，无认证可 50 req/5s
    """

    name = "crossref"
    _API_URL = "https://api.crossref.org/works"

    def __init__(self):
        super().__init__()
        self._logger = get_logger("bib_manager.crossref")

    def _get_ua(self) -> str:
        from backend.core.config import get_config

        return get_config().http.user_agent

    async def lookup_by_doi(self, doi: str) -> DoiLookupResult | None:
        try:
            response = await self._get_http_client().get(
                f"{self._API_URL}/{doi}",
                headers={"User-Agent": self._get_ua()},
                timeout=30.0,
            )
            response.raise_for_status()

            data = response.json()
            message = data.get("message", {})
            if not message:
                return None

            return self._parse_message(doi, message)

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                self._logger.info(f"DOI not found in Crossref: {doi}")
            else:
                self._logger.warning(f"Crossref API error: {e.response.status_code}")
            return None
        except Exception as e:
            self._logger.warning(f"Crossref lookup failed: {e}")
            return None

    async def health_check(self) -> bool:
        try:
            response = await self._get_http_client().get(
                "https://api.crossref.org/works/10.1038/12345",
                headers={"User-Agent": self._get_ua()},
                timeout=10.0,
            )
            return response.status_code in (200, 404)
        except Exception:
            return False

    def _parse_message(self, doi: str, message: dict) -> DoiLookupResult:
        authors = []
        for author in message.get("author", []):
            given = author.get("given", "")
            family = author.get("family", "")
            if given and family:
                authors.append(f"{family} {given}")
            elif family:
                authors.append(family)
            elif given:
                authors.append(given)

        year = ""
        issued = message.get("issued", {})
        if issued and issued.get("date-parts"):
            date_parts = issued["date-parts"][0]
            if date_parts and date_parts[0]:
                year = str(date_parts[0])

        journal = ""
        if message.get("container-title"):
            journal = (
                message["container-title"][0] if message["container-title"] else ""
            )

        title = ""
        if message.get("title"):
            title = message["title"][0] if message["title"] else ""

        volume = str(message.get("volume", ""))
        issue = str(message.get("issue", ""))
        page = message.get("page", "")

        publisher = message.get("publisher", "")

        abstract = message.get("abstract", "")
        if abstract:
            abstract = re.sub(r"<[^>]+>", "", abstract)

        keywords = message.get("subject", [])

        isbn_list = message.get("ISBN", [])
        isbn = isbn_list[0] if isbn_list else ""

        issn_list = message.get("ISSN", [])
        issn = issn_list[0] if issn_list else ""

        self._logger.info(f"Crossref lookup: {title[:50]}...")
        return DoiLookupResult(
            title=title,
            authors=authors,
            year=year,
            journal=journal,
            volume=volume,
            issue=issue,
            pages=page,
            doi=doi,
            publisher=publisher,
            abstract=abstract,
            url=f"https://doi.org/{doi}",
            isbn=isbn,
            issn=issn,
            keywords=keywords,
            data_source=self.name,
        )
