"""PubMed API 提供者 — NCBI E-utilities 生物医学文献检索。"""

import asyncio
import time
from typing import Any

import httpx

from backend.core.config import get_config
from backend.core.logger import get_logger

from ..base import BaseResearchProvider
from ..models import (
    Author,
    RelatedWorksResult,
    ScholarlyWork,
    SearchScholarlyResult,
    WorkDetailResult,
)

_API_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"


def _parse_pubmed_xml(xml_text: str) -> list[dict[str, Any]]:
    import xml.etree.ElementTree as ET

    articles = []
    root = ET.fromstring(xml_text)
    for article_elem in root.findall(".//PubmedArticle"):
        article: dict[str, Any] = {}
        medline = article_elem.find(".//MedlineCitation")
        if medline is None:
            continue
        pmid_elem = medline.find(".//PMID")
        if pmid_elem is not None and pmid_elem.text:
            article["pmid"] = pmid_elem.text

        article_data = medline.find(".//Article")
        if article_data is None:
            continue
        title_elem = article_data.find(".//ArticleTitle")
        if title_elem is not None and title_elem.text:
            article["title"] = title_elem.text

        abstract_parts = []
        for abs_elem in article_data.findall(".//AbstractText"):
            label = abs_elem.get("Label", "")
            text = abs_elem.text or ""
            if label:
                abstract_parts.append(f"{label}: {text}")
            else:
                abstract_parts.append(text)
        article["abstract"] = " ".join(abstract_parts)

        authors = []
        for author_elem in article_data.findall(".//Author"):
            last = author_elem.findtext("LastName") or ""
            fore = author_elem.findtext("ForeName") or ""
            name = f"{fore} {last}".strip()
            affiliation = ""
            affil_elem = author_elem.find(".//AffiliationInfo/Affiliation")
            if affil_elem is not None and affil_elem.text:
                affiliation = affil_elem.text
            authors.append({"name": name, "affiliation": affiliation})
        article["authors"] = authors

        journal_elem = article_data.find(".//Journal")
        if journal_elem is not None:
            title_j = journal_elem.findtext("Title") or ""
            article["journal"] = title_j
            issue_elem = journal_elem.find(".//JournalIssue")
            if issue_elem is not None:
                volume = issue_elem.findtext("Volume") or ""
                issue = issue_elem.findtext("Issue") or ""
                article["volume"] = volume
                article["issue"] = issue
                pub_date = issue_elem.find(".//PubDate")
                if pub_date is not None:
                    yr = pub_date.findtext("Year")
                    if yr:
                        article["year"] = int(yr)

        doi = ""
        for eid_elem in article_data.findall(".//ELocationID"):
            if eid_elem.get("EIdType") == "doi" and eid_elem.text:
                doi = eid_elem.text
        if not doi:
            for did_elem in article_elem.findall(".//ArticleId"):
                if did_elem.get("IdType") == "doi" and did_elem.text:
                    doi = did_elem.text
        article["doi"] = doi

        pmc = ""
        for other_elem in article_elem.findall(".//ArticleId"):
            if other_elem.get("IdType") == "pmc" and other_elem.text:
                pmc = other_elem.text
        article["pmc"] = pmc

        pub_types = [
            pt.text
            for pt in article_data.findall(".//PublicationType")
            if pt is not None and pt.text
        ]
        article["publication_types"] = pub_types

        articles.append(article)
    return articles


def _build_work_from_pubmed(article: dict[str, Any]) -> ScholarlyWork:
    authors = [
        Author(
            name=author.get("name", "Unknown author"),
            institution=author.get("affiliation", ""),
        )
        for author in article.get("authors", [])
    ]
    pub_types = article.get("publication_types", [])
    work_type = ""
    if "Review" in pub_types:
        work_type = "review"
    elif "Journal Article" in pub_types:
        work_type = "journal_article"

    return ScholarlyWork(
        pmid=article.get("pmid", ""),
        pmcid=article.get("pmc", ""),
        doi=article.get("doi", ""),
        title=article.get("title", "Untitled work"),
        authors=authors,
        year=article.get("year", 0),
        journal=article.get("journal", ""),
        volume=str(article.get("volume", "")),
        issue=str(article.get("issue", "")),
        work_type=work_type,
        abstract=article.get("abstract", ""),
        source_records=["pubmed"],
    )


class PubMedProvider(BaseResearchProvider):
    name = "pubmed"

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client
        self._logger = get_logger("scholarly_research.pubmed")

    def _api_key(self) -> str:
        cfg = get_config().features.function_calling.scholarly_research
        return cfg.providers.pubmed.api_key

    def _get_user_agent(self) -> str:
        return get_config().http.user_agent

    def _tool(self) -> str:
        return "latexcellence"

    def _email(self) -> str:
        key = self._api_key()
        return key if "@" in key else ""

    async def _esearch(self, query: str, max_results: int) -> list[str]:
        params: dict[str, Any] = {
            "db": "pubmed",
            "term": query,
            "retmax": min(max_results, 100),
            "retmode": "json",
            "tool": self._tool(),
        }
        email = self._email()
        if email:
            params["email"] = email
        api_key = self._api_key()
        if api_key and "@" not in api_key:
            params["api_key"] = api_key

        response = await self._client.get(
            f"{_API_BASE}/esearch.fcgi",
            params=params,
            headers={"User-Agent": self._get_user_agent()},
            timeout=get_config().features.function_calling.scholarly_research.timeout_seconds,
        )
        response.raise_for_status()
        data = response.json()
        return data.get("esearchresult", {}).get("idlist", [])

    async def _efetch(self, pmid_list: list[str]) -> str:
        params: dict[str, Any] = {
            "db": "pubmed",
            "id": ",".join(pmid_list),
            "retmode": "xml",
            "tool": self._tool(),
        }
        email = self._email()
        if email:
            params["email"] = email
        api_key = self._api_key()
        if api_key and "@" not in api_key:
            params["api_key"] = api_key

        response = await self._client.get(
            f"{_API_BASE}/efetch.fcgi",
            params=params,
            headers={"User-Agent": self._get_user_agent()},
            timeout=get_config().features.function_calling.scholarly_research.timeout_seconds,
        )
        response.raise_for_status()
        return response.text

    async def search(self, query: str, **filters: Any) -> SearchScholarlyResult:
        return await self.search_works(query=query, **filters)

    async def search_works(
        self,
        query: str,
        year_from: int | None = None,
        year_to: int | None = None,
        authors: list[str] | None = None,
        work_types: list[str] | None = None,
        sort_by: str = "relevance",
        max_results: int | None = None,
    ) -> SearchScholarlyResult:
        from .._utils import _limit_results

        if not query.strip():
            raise ValueError("query must not be empty")
        limit = _limit_results(max_results, 20)
        pubmed_query = query.strip()
        if year_from or year_to:
            date_range = f"{year_from or '1800'}:{year_to or '3000'}"
            pubmed_query = f"({pubmed_query}) AND {date_range}[dp]"

        started = time.monotonic()
        try:
            pmid_list = await self._esearch(pubmed_query, limit)
        except Exception as exc:
            self._logger.warning("PubMed esearch failed: %s", exc)
            return SearchScholarlyResult(query=query, per_page=limit, source=self.name)

        if not pmid_list:
            return SearchScholarlyResult(query=query, per_page=limit, source=self.name)

        try:
            xml_text = await self._efetch(pmid_list)
        except Exception as exc:
            self._logger.warning("PubMed efetch failed: %s", exc)
            return SearchScholarlyResult(query=query, per_page=limit, source=self.name)

        articles = _parse_pubmed_xml(xml_text)
        works = [_build_work_from_pubmed(article) for article in articles]
        elapsed_ms = (time.monotonic() - started) * 1000
        return SearchScholarlyResult(
            query=query,
            results=works,
            total_estimated=int(len(pmid_list)),
            per_page=limit,
            source=self.name,
            search_time_ms=elapsed_ms,
        )

    async def get_details(self, work_id: str) -> WorkDetailResult | None:
        identifier = work_id.strip()
        pmid = identifier
        try:
            xml_text = await self._efetch([pmid])
        except Exception:
            return None
        articles = _parse_pubmed_xml(xml_text)
        if not articles:
            return None
        return WorkDetailResult(
            work=_build_work_from_pubmed(articles[0]), source=self.name
        )

    async def find_related(
        self, seed_id: str | None = None, relation_type: str = "similar", **kwargs: Any
    ) -> RelatedWorksResult:
        return RelatedWorksResult(seed_id=seed_id or "", relation_type=relation_type)
