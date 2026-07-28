"""DataCite API 元数据提供者（补全层）。"""

import httpx

from backend.core.logger import get_logger
from backend.modules.bib_manager.models import DoiLookupResult
from backend.modules.bib_manager.providers.base import BaseMetadataProvider


class DataCiteProvider(BaseMetadataProvider):
    """DataCite API 提供者——补全层。

    API 端点：https://api.datacite.org/dois/{doi}
    无认证即可使用。
    """

    name = "datacite"
    _API_URL = "https://api.datacite.org/dois"

    def __init__(self):
        self._logger = get_logger("bib_manager.datacite")

    def _get_ua(self) -> str:
        from backend.core.config import get_config

        return get_config().http.user_agent

    async def lookup_by_doi(self, doi: str) -> DoiLookupResult | None:
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(
                    f"{self._API_URL}/{doi}",
                    headers={
                        "User-Agent": self._get_ua(),
                        "Accept": "application/vnd.api+json",
                    },
                )
                response.raise_for_status()

            data = response.json()
            attributes = data.get("data", {}).get("attributes", {})
            if not attributes:
                return None

            return self._parse_attributes(doi, attributes)

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                self._logger.info(f"DOI not found in DataCite: {doi}")
            else:
                self._logger.warning(f"DataCite API error: {e.response.status_code}")
            return None
        except Exception as e:
            self._logger.warning(f"DataCite lookup failed: {e}")
            return None

    async def health_check(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(
                    f"{self._API_URL}/10.1038/12345",
                    headers={
                        "User-Agent": self._get_ua(),
                        "Accept": "application/vnd.api+json",
                    },
                )
                return response.status_code in (200, 404)
        except Exception:
            return False

    def _parse_attributes(self, doi: str, attrs: dict) -> DoiLookupResult:
        titles = attrs.get("titles", [])
        title = titles[0].get("title", "") if titles else ""

        authors = []
        for creator in attrs.get("creators", []):
            name = creator.get("name", "")
            family = creator.get("familyName", "")
            given = creator.get("givenName", "")
            if family and given:
                authors.append(f"{family} {given}")
            elif name:
                authors.append(name)

        year = str(attrs.get("publicationYear", ""))

        publisher = attrs.get("publisher", "")

        abstracts = attrs.get("descriptions", [])
        abstract = ""
        for desc in abstracts:
            if desc.get("descriptionType") == "Abstract":
                abstract = desc.get("description", "")
                break

        subjects = attrs.get("subjects", [])
        keywords = [s.get("subject", "") for s in subjects if s.get("subject")]

        isbn_list = attrs.get("isbn", [])
        isbn = isbn_list[0] if isbn_list else ""

        issn_list = attrs.get("issn", [])
        issn = issn_list[0] if issn_list else ""

        self._logger.info(f"DataCite lookup: {title[:50]}...")
        return DoiLookupResult(
            title=title,
            authors=authors,
            year=year,
            journal="",
            volume="",
            issue="",
            pages="",
            doi=doi,
            publisher=publisher,
            abstract=abstract,
            url=attrs.get("url", f"https://doi.org/{doi}"),
            isbn=isbn,
            issn=issn,
            keywords=keywords,
            data_source=self.name,
        )
