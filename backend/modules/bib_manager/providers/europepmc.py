"""Europe PMC API 元数据提供者（补全层）。"""

import re

import httpx

from backend.core.logger import get_logger
from backend.modules.bib_manager.models import DoiLookupResult
from backend.modules.bib_manager.providers.base import BaseMetadataProvider


class EuropePMCProvider(BaseMetadataProvider):
    """Europe PMC API 提供者——补全层。

    API 端点：https://www.ebi.ac.uk/europepmc/webservices/rest/search
    请求格式：?query=EXT_ID:{doi}&format=json&resultType=core
    无认证即可使用。
    """

    name = "europe_pmc"
    _API_URL = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"

    def __init__(self):
        super().__init__()
        self._logger = get_logger("bib_manager.europepmc")

    def _get_ua(self) -> str:
        from backend.core.config import get_config

        return get_config().http.user_agent

    async def lookup_by_doi(self, doi: str) -> DoiLookupResult | None:
        try:
            response = await self._get_http_client().get(
                self._API_URL,
                params={
                    "query": f"EXT_ID:{doi}",
                    "format": "json",
                    "resultType": "core",
                },
                headers={"User-Agent": self._get_ua()},
                timeout=30.0,
            )
            response.raise_for_status()

            data = response.json()
            results = data.get("resultList", {}).get("result", [])
            if not results:
                return None

            return self._parse_result(doi, results[0])

        except httpx.HTTPStatusError as e:
            self._logger.warning(f"Europe PMC API error: {e.response.status_code}")
            return None
        except Exception as e:
            self._logger.warning(f"Europe PMC lookup failed: {e}")
            return None

    async def health_check(self) -> bool:
        try:
            response = await self._get_http_client().get(
                self._API_URL,
                params={
                    "query": "EXT_ID:10.1038/12345",
                    "format": "json",
                    "resultType": "core",
                },
                headers={"User-Agent": self._get_ua()},
                timeout=10.0,
            )
            return response.status_code == 200
        except Exception:
            return False

    def _parse_result(self, doi: str, result: dict) -> DoiLookupResult:
        title = result.get("title", "")

        author_str = result.get("authorString", "")
        if author_str:
            authors = [a.strip() for a in author_str.split(",") if a.strip()]
        else:
            authors = []

        year = str(result.get("pubYear", ""))

        journal = result.get("journalTitle", "")

        volume = str(result.get("journalVolume", ""))
        issue = str(result.get("issue", ""))
        page_info = result.get("pageInfo", "")
        pages_match = re.match(r"(\d+)-(\d+)", page_info) if page_info else None
        pages = page_info

        publisher = result.get("publisher", "")

        abstract = result.get("abstractText", "")
        if abstract:
            abstract = re.sub(r"<[^>]+>", "", abstract)

        keywords = []
        keyword_list = result.get("keywordList", {}).get("keyword", [])
        for kw in keyword_list:
            if isinstance(kw, str):
                keywords.append(kw)
            elif isinstance(kw, dict):
                keywords.append(kw.get("value", ""))

        issn = result.get("issn", "")

        source = result.get("source", "")
        url = ""
        if source == "PMC":
            pmcid = result.get("pmcid", "")
            if pmcid:
                url = f"https://www.ncbi.nlm.nih.gov/pmc/articles/{pmcid}/"
        if not url:
            url = result.get("doi", f"https://doi.org/{doi}")

        self._logger.info(f"Europe PMC lookup: {title[:50]}...")
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
