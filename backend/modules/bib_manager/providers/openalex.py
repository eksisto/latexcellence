"""OpenAlex API 元数据提供者（补全层）。"""

import re

import httpx

from backend.core.logger import get_logger
from backend.modules.bib_manager.models import DoiLookupResult
from backend.modules.bib_manager.providers.base import BaseMetadataProvider


class OpenAlexProvider(BaseMetadataProvider):
    """OpenAlex API 提供者——补全层。

    API 端点：https://api.openalex.org/works/doi:{doi}
    无认证开放 API。
    """

    name = "openalex"
    _API_URL = "https://api.openalex.org/works"

    def __init__(self):
        self._logger = get_logger("bib_manager.openalex")

    def _get_ua(self) -> str:
        from backend.core.config import get_config

        return get_config().http.user_agent

    async def lookup_by_doi(self, doi: str) -> DoiLookupResult | None:
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(
                    f"{self._API_URL}/doi:{doi}",
                    headers={
                        "User-Agent": self._get_ua(),
                    },
                )
                response.raise_for_status()

            data = response.json()
            if not data or data.get("id") is None:
                return None

            return self._parse_work(doi, data)

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                self._logger.info(f"DOI not found in OpenAlex: {doi}")
            else:
                self._logger.warning(f"OpenAlex API error: {e.response.status_code}")
            return None
        except Exception as e:
            self._logger.warning(f"OpenAlex lookup failed: {e}")
            return None

    async def health_check(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(
                    f"{self._API_URL}/doi:10.1038/12345",
                    headers={"User-Agent": self._get_ua()},
                )
                return response.status_code in (200, 404)
        except Exception:
            return False

    def _parse_work(self, doi: str, data: dict) -> DoiLookupResult:
        title = data.get("title", "")

        authors = []
        for authorship in data.get("authorships", []):
            author = authorship.get("author", {})
            display_name = author.get("display_name", "")
            if display_name:
                authors.append(display_name)

        year = str(data.get("publication_year", ""))

        journal = ""
        primary_location = data.get("primary_location", {})
        source = primary_location.get("source", {})
        if source:
            journal = source.get("display_name", "")

        biblio = data.get("biblio", {})
        volume = str(biblio.get("volume", ""))
        issue = str(biblio.get("issue", ""))
        first_page = biblio.get("first_page", "")
        last_page = biblio.get("last_page", "")
        pages = (
            f"{first_page}-{last_page}"
            if first_page and last_page
            else first_page or ""
        )

        publisher = ""
        source_obj = primary_location.get("source", {}) if primary_location else {}
        if source_obj:
            publisher = source_obj.get("host_organization_name", "")

        abstract = ""
        abstract_inverted = data.get("abstract_inverted_index")
        if abstract_inverted and isinstance(abstract_inverted, dict):
            abstract = self._reconstruct_abstract(abstract_inverted)

        keywords = []
        for concept in data.get("concepts", []):
            kw = concept.get("display_name", "")
            if kw:
                keywords.append(kw)

        is_oa = data.get("open_access", {}).get("is_oa", False)
        oa_url = data.get("open_access", {}).get("oa_url", "")
        url = oa_url or f"https://doi.org/{doi}"

        issn_list = data.get("primary_location", {}).get("source", {}).get("issn_l", "")
        issn = issn_list[0] if isinstance(issn_list, list) and issn_list else ""

        self._logger.info(f"OpenAlex lookup: {title[:50]}...")
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
            issn=issn,
            keywords=keywords,
            data_source=self.name,
        )

    def _reconstruct_abstract(self, inverted_index: dict) -> str:
        """重建 OpenAlex 的倒排索引摘要。"""
        word_positions = []
        for word, positions in inverted_index.items():
            for pos in positions:
                word_positions.append((pos, word))

        word_positions.sort(key=lambda x: x[0])
        return " ".join(word for _, word in word_positions)
