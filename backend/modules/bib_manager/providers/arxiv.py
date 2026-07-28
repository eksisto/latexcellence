"""arXiv API 元数据提供者（预印本专用）。"""

import re
import xml.etree.ElementTree as ET

import httpx

from backend.core.logger import get_logger
from backend.modules.bib_manager.models import DoiLookupResult
from backend.modules.bib_manager.providers.base import (
    BaseMetadataProvider,
    extract_arxiv_id,
    is_arxiv_doi,
)


class ArxivProvider(BaseMetadataProvider):
    """arXiv API 提供者——处理 arXiv DOI 的预印本查询。

    API 端点：https://export.arxiv.org/api/query?id_list={arxiv_id}
    响应格式：Atom XML
    仅处理 10.48550/arXiv.* 开头的 DOI，非 arXiv DOI 返回 None。
    """

    name = "arxiv"
    _API_URL = "https://export.arxiv.org/api/query"

    def __init__(self):
        self._logger = get_logger("bib_manager.arxiv")

    async def lookup_by_doi(self, doi: str) -> DoiLookupResult | None:
        if not is_arxiv_doi(doi):
            return None

        arxiv_id = extract_arxiv_id(doi)
        if not arxiv_id:
            return None

        self._logger.info(f"arXiv lookup: {arxiv_id}")
        try:
            async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
                response = await client.get(
                    self._API_URL,
                    params={
                        "id_list": arxiv_id,
                        "max_results": "1",
                    },
                )
                response.raise_for_status()

            return self._parse_atom(doi, arxiv_id, response.text)

        except Exception as e:
            self._logger.warning(f"arXiv lookup failed: {e}")
            return None

    async def health_check(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(
                    self._API_URL,
                    params={"id_list": "1706.03762", "max_results": "1"},
                )
                return response.status_code == 200
        except Exception:
            return False

    def _parse_atom(
        self, doi: str, arxiv_id: str, xml_text: str
    ) -> DoiLookupResult | None:
        ns = {
            "atom": "http://www.w3.org/2005/Atom",
            "arxiv": "http://arxiv.org/schemas/atom",
        }

        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as e:
            self._logger.warning(f"arXiv XML parse error: {e}")
            return None

        entry = root.find("atom:entry", ns)
        if entry is None:
            return None

        title_el = entry.find("atom:title", ns)
        title = title_el.text.strip() if title_el is not None and title_el.text else ""

        authors = []
        for author_el in entry.findall("atom:author", ns):
            name_el = author_el.find("atom:name", ns)
            if name_el is not None and name_el.text:
                authors.append(name_el.text.strip())

        year = ""
        published_el = entry.find("atom:published", ns)
        if published_el is not None and published_el.text:
            published = published_el.text.strip()
            match = re.match(r"(\d{4})", published)
            if match:
                year = match.group(1)

        summary_el = entry.find("atom:summary", ns)
        abstract = ""
        if summary_el is not None and summary_el.text:
            abstract = summary_el.text.strip()
            abstract = re.sub(r"\s+", " ", abstract)

        categories = []
        for cat_el in entry.findall("atom:category", ns):
            term = cat_el.get("term", "")
            if term:
                categories.append(term)

        self._logger.info(f"arXiv lookup: {title[:50]}...")
        return DoiLookupResult(
            title=title,
            authors=authors,
            year=year,
            journal="arXiv preprint",
            volume="",
            issue="",
            pages="",
            doi=doi,
            publisher="arXiv",
            abstract=abstract,
            url=f"https://arxiv.org/abs/{arxiv_id}",
            isbn="",
            issn="",
            keywords=categories,
            data_source=self.name,
        )
