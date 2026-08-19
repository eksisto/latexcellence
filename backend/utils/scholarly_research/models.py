"""学术研究工具的统一数据模型。"""

from pydantic import BaseModel, Field

from backend.utils.tool_result_protocol import format_result_status


class FieldProvenance(BaseModel):
    """字段值的来源与冲突状态。"""

    field: str
    provider: str
    value: str = ""
    conflict: bool = False


class WorkVersion(BaseModel):
    """同一作品的预印本、会议版或正式版。"""

    identifier: str
    version_type: str = "unknown"
    provider: str = ""
    title: str = ""


class Author(BaseModel):
    """论文作者及其可追溯身份信息。"""

    name: str
    orcid: str = ""
    institution: str = ""


class Concept(BaseModel):
    """OpenAlex 主题或概念标签。"""

    id: str
    name: str
    score: float = 0.0
    level: int = 0


class ScholarlyWork(BaseModel):
    """跨数据源统一的学术作品记录。"""

    openalex_id: str = ""
    doi: str = ""
    semantic_scholar_id: str = (
        ""  # Future: will be populated when Semantic Scholar provider is added
    )
    pmid: str = ""
    pmcid: str = ""
    arxiv_id: str = ""

    title: str
    authors: list[Author] = Field(default_factory=list)
    year: int = 0
    journal: str = ""
    volume: str = ""
    issue: str = ""
    pages: str = ""
    publisher: str = ""
    work_type: str = ""
    abstract: str = ""
    keywords: list[str] = Field(default_factory=list)
    concepts: list[Concept] = Field(default_factory=list)

    citation_count: int = 0
    normalized_citation_percentile: float = 0.0

    is_oa: bool = False
    oa_url: str = ""
    oa_status: str = ""

    source_records: list[str] = Field(default_factory=lambda: ["openalex"])
    is_preprint_of: str = ""
    has_preprint: bool = False

    referenced_works_count: int = 0
    cited_by_count: int = 0

    identifiers: dict[str, str] = Field(default_factory=dict)
    versions: list[WorkVersion] = Field(default_factory=list)
    field_provenance: list[FieldProvenance] = Field(default_factory=list)
    publication_status: str = "unknown"
    retraction_status: str = "unknown"
    ranking_explanation: dict[str, float | str] = Field(default_factory=dict)


class SearchScholarlyResult(BaseModel):
    """学术作品检索结果。"""

    query: str
    results: list[ScholarlyWork] = Field(default_factory=list)
    total_estimated: int = 0
    page: int = 1
    per_page: int = 20
    source: str = "openalex"
    search_time_ms: float = 0.0
    unsupported_filters: list[str] = Field(default_factory=list)

    def format_for_llm(self, max_results: int = 10) -> str:
        items = self.results[:max_results]
        if not items:
            return f'No scholarly works found for "{self.query}".'
        total = self.total_estimated or len(self.results)
        truncated = total > len(items) or len(self.results) > len(items)
        lines = [f'Search "{self.query}" returned approximately ' f"{total} result(s):"]
        for index, work in enumerate(items, 1):
            authors = ", ".join(author.name for author in work.authors[:3])
            if len(work.authors) > 3:
                authors += " et al."
            lines.append(
                f"[{index}] {work.title} | {authors} ({work.year}) | "
                f"{work.journal} | Cited: {work.citation_count}"
            )
            if work.openalex_id:
                lines.append(f"    OpenAlex ID: {work.openalex_id}")
            if work.doi:
                lines.append(f"    DOI: {work.doi}")
            if work.is_oa and work.oa_url:
                lines.append(f"    OA: {work.oa_url}")
            if work.abstract:
                suffix = "..." if len(work.abstract) > 300 else ""
                lines.append(f"    Abstract: {work.abstract[:300]}{suffix}")
            lines.append("")
        lines.append(
            format_result_status(
                shown=len(items),
                total=total,
                truncated=truncated,
                next_step=(
                    "Refine the query or increase max_results to fetch more; "
                    "or call get_work_details for a specific work."
                ),
            )
        )
        return "\n".join(lines)


class WorkDetailResult(BaseModel):
    """单篇作品详情。"""

    work: ScholarlyWork
    source: str = "openalex"

    def format_for_llm(self) -> str:
        work = self.work
        lines = [
            f"Title: {work.title}",
            f"Authors: {', '.join(author.name for author in work.authors)}",
            f"Year: {work.year}",
            f"Journal: {work.journal}",
            f"Work Type: {work.work_type}",
            f"Citations: {work.citation_count}",
            f"DOI: {work.doi}",
            f"OpenAlex ID: {work.openalex_id}",
        ]
        if work.is_oa:
            lines.extend([f"OA Status: {work.oa_status}", f"OA URL: {work.oa_url}"])
        if work.abstract:
            lines.append(f"\nAbstract:\n{work.abstract}")
        if work.keywords:
            lines.append(f"\nKeywords: {', '.join(work.keywords)}")
        if work.concepts:
            top = sorted(
                work.concepts, key=lambda concept: concept.score, reverse=True
            )[:5]
            lines.append(
                "\nConcepts: "
                + ", ".join(f"{concept.name}({concept.score:.2f})" for concept in top)
            )
        return "\n".join(lines)


class RelatedWorksResult(BaseModel):
    """引文追踪或相似作品结果。"""

    seed_id: str
    relation_type: str
    works: list[ScholarlyWork] = Field(default_factory=list)
    total: int = 0

    def format_for_llm(self, max_results: int = 15) -> str:
        items = self.works[:max_results]
        if not items:
            return f"No {self.relation_type} works found."
        labels = {
            "references": "References",
            "cited_by": "Cited by",
            "similar": "Similar works",
        }
        total = self.total if self.total else len(self.works)
        truncated = total > len(items) or len(self.works) > len(items)
        lines = [
            f"{labels.get(self.relation_type, self.relation_type)} " f"({total} total):"
        ]
        for index, work in enumerate(items, 1):
            authors = ", ".join(author.name for author in work.authors[:3])
            if len(work.authors) > 3:
                authors += " et al."
            lines.append(
                f"[{index}] {work.title} | {authors} ({work.year}) | "
                f"{work.journal} | Cited: {work.citation_count}"
            )
            if work.openalex_id:
                lines.append(f"    OpenAlex ID: {work.openalex_id}")
            if work.doi:
                lines.append(f"    DOI: {work.doi}")
            lines.append("")
        lines.append(
            format_result_status(
                shown=len(items),
                total=total,
                truncated=truncated,
                next_step=(
                    "Increase max_results or narrow the query to fetch more "
                    "related works."
                ),
            )
        )
        return "\n".join(lines)


class FullTextResult(BaseModel):
    """可获取的正文片段及开放获取定位信息。"""

    work_id: str
    sections: dict[str, str] = Field(default_factory=dict)
    oa_url: str = ""
    note: str = ""
    available_sections: list[str] = Field(default_factory=list)
    missing_sections: list[str] = Field(default_factory=list)
    extraction_confidence: float = 0.0
    source_kind: str = "metadata"

    def format_for_llm(self) -> str:
        lines = [f"Work ID: {self.work_id}"]
        if self.oa_url:
            lines.append(f"OA URL: {self.oa_url}")
        if self.note:
            lines.append(f"Note: {self.note}")
        for section_name, text in self.sections.items():
            lines.append(f"\n--- {section_name.upper()} ---\n{text}")
        return "\n".join(lines)


class ResearchDataResource(BaseModel):
    """研究数据资源的轻量展示模型。"""

    name: str
    url: str = ""
    description: str = ""
    resource_type: str = ""
    related_work_doi: str = ""

    def format_for_llm(self) -> str:
        return f"[{self.resource_type}] {self.name} - {self.description} ({self.url})"
