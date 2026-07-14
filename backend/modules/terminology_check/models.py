"""
术语一致性检查 Pydantic 模型。

定义术语抽取、一致性问题和检查结果的数据结构。
"""

from typing import Optional

from pydantic import BaseModel, ConfigDict


class ExtractedTerm(BaseModel):
    """LLM 从段落中抽取的单个术语"""
    model_config = ConfigDict(extra="forbid")

    term: str
    term_type: str = "concept"
    context: str = ""


class TermExtractionResult(BaseModel):
    """LLM 术语抽取的响应格式"""
    model_config = ConfigDict(extra="forbid")

    paragraph_index: int
    terms: list[ExtractedTerm] = []


class TermChange(BaseModel):
    """单条术语修改建议，与 typo_check 的 ChangeItem 格式兼容"""
    model_config = ConfigDict(extra="forbid")

    type: str = "inconsistency"
    concept: str = ""
    original: str
    corrected: str
    explanation: str = ""


class TermParagraphResult(BaseModel):
    """逐段检查结果，兼容 useParagraphCheck 前端模式"""
    model_config = ConfigDict(extra="forbid")

    paragraph_index: int
    original: str
    corrected: str
    terms: list[str] = []
    changes: list[TermChange] = []


class TermCluster(BaseModel):
    """LLM 语义聚类结果中的单组同义术语"""
    model_config = ConfigDict(extra="forbid")

    recommended: str
    variants: list[str]


class TermClusterResult(BaseModel):
    """LLM 语义聚类响应"""
    model_config = ConfigDict(extra="forbid")

    clusters: list[TermCluster] = []
