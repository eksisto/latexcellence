# 术语一致性检查模块 `terminology_check/`

## 设计特点

术语一致性检查使用**两阶段 LLM 调用**（提取 + 语义聚类），而非固定算法。
术语一致性本质上是语义问题——"Deep Learning"、"deep learning"、"深度学习"、"DL"
在文本层面完全不同，但语义上是同一概念。

| 维度 | 术语检查 | typo_check / expression_opt |
|------|---------|---------------------------|
| LLM 调用次数 | N+1（N 段提取 + 1 次聚类） | N（逐段检查） |
| 跨段落分析 | 是（聚类基于全文术语） | 否（逐段独立） |
| 检测方式 | LLM 语义聚类 | LLM 逐段文本修改 |
| 修复粒度 | 术语替换（建议统一为推荐项） | 全段落文本替换 |
| 用户自定义 | 支持自定义替换文本 | 仅接受或忽略建议 |

## `models.py` — Pydantic 模型

```python
class ExtractedTerm(BaseModel):
    """LLM 从段落中抽取的单个术语"""
    term: str
    term_type: str = "concept"   # concept / abbreviation / proper_noun
    context: str = ""

class TermExtractionResult(BaseModel):
    """LLM 术语抽取响应"""
    paragraph_index: int
    terms: list[ExtractedTerm] = []

class TermChange(BaseModel):
    """单条术语修改建议，与 typo_check 的 TypoChangeItem 格式兼容"""
    type: str = "inconsistency"
    concept: str = ""
    original: str
    corrected: str
    explanation: str = ""

class TermCluster(BaseModel):
    """LLM 语义聚类结果中的单组同义术语"""
    recommended: str
    variants: list[str]

class TermClusterResult(BaseModel):
    """LLM 语义聚类响应"""
    clusters: list[TermCluster] = []

class TermParagraphResult(BaseModel):
    """逐段检查结果，兼容 useParagraphCheck 前端模式"""
    paragraph_index: int
    original: str
    corrected: str
    terms: list[str] = []
    changes: list[TermChange] = []
```

## `extractor.py` — LLM 术语提取与语义聚类

### 核心方法

| 方法 | 功能 |
| ---- | ---- |
| `extract_terms(paragraph_index, plain_text)` | 调用 LLM 从单段提取关键术语，返回 `TermItem` 列表 |
| `extract_terms_fallback(plain_text)` | 无 LLM 时的规则降级：基于正则提取大写缩写（`CNN`, `RNN` 等） |
| `cluster_terms(unique_terms)` | 调用 LLM 对去重后的术语列表进行语义聚类，返回聚类列表 |

### 术语提取 System Prompt

```
You are a terminology extractor for academic LaTeX documents.
CRITICAL RULES:
1. Markers like __CMD_0000__ represent LaTeX commands. Do NOT extract them.
2. Only extract terms meaningful for consistency checking.
3. Preserve exact wording as it appears in the text.
```

### 语义聚类 System Prompt

```
You are a terminology consistency analyzer for academic documents.
1. Group terms that refer to the SAME concept (semantically identical).
2. For each group, select the most standard term as "recommended".
3. List ALL terms as "variants", INCLUDING the recommended term if it was extracted.
4. Only output groups with 2+ variants.
```

聚类 LLM 返回格式（强制 JSON）：
```json
{
  "clusters": [
    {"recommended": "深度学习", "variants": ["Deep Learning", "deep learning", "深度学习", "DL"]}
  ]
}
```

## `service.py` — 三步走业务逻辑

### 核心方法

| 方法 | 功能 |
| ---- | ---- |
| `preview_paragraphs(file_path, resolve_input, workspace_id)` | 同步：提取段落 + 剥离 LaTeX + 缓存 marker_map |
| `check_terminology(file_path, paragraph_indices, resolve_input, workspace_id)` | 异步：三步走流程（提取 → 聚类 → 生成结果） |
| `check_terminology_stream(file_path, resolve_input, workspace_id)` | 异步生成器：提取阶段逐段 yield progress，聚类后 yield 所有 result |
| `apply_changes(file_path, changes, workspace_id, operation_id)` | 同步：还原 marker + 原子写回；marker_map 缺失时跳过该段并返回 `errors` |
| `_build_results(all_para_data, all_terms, clusters)` | 程序解析聚类结果，为每个段落生成修正文本 |
| `_build_results_no_llm(all_para_data)` | 无 LLM 降级：返回段落术语标签，不生成 changes |

### 三步走流程

```
Phase 1: 逐段提取
  按 batch_size 分批，每批 asyncio.gather 并发调用 LLM
  所有术语收集到 all_terms 列表

Phase 2: 语义聚类
  _deduplicate_terms(all_terms) → unique_terms（精确匹配去重）
  _extractor.cluster_terms(unique_terms) → clusters

Phase 3: 生成修正结果
  构建 replace_map: variant → recommended（排除推荐项自身）
  为每个段落找到需要替换的变体，生成 changes + corrected 文本
  按变体长度降序替换，防止短变体误匹配长变体子串
```

### 三层防护（与 typo_check 一致）

| 层级 | 防护内容 | 失败处理 |
| ---- | -------- | -------- |
| 第 1 层 | `retry_with_validation()`（最多 3 次） | LLM 输出不符 schema 时反馈重试 |
| 第 2 层 | Marker 匹配验证 | 不一致时回退到原始文本 |
| 第 3 层 | 异常兜底 | LLM 调用异常时返回原始文本 + 空 changes |

### 辅助函数

- `_deduplicate_terms(all_terms)` — 仅当字符完全匹配时视为重复，保持首次出现顺序

## `router.py` — API 路由

Prefix: `/api/terminology-check`, Tag: `terminology_check`

| 方法 | 路径 | 功能 |
|---|---|---|
| `GET` | `/preview` | 预览段落（剥离 LaTeX 后的纯文本） |
| `POST` | `/check` | LLM 逐段提取术语 + 语义聚类检查一致性 |
| `POST` | `/apply` | 将审核通过的术语修改还原 marker 并写回文件 |
| `GET` | `/stream/{workspace_id}` | SSE 流式推送：提取进度 → 聚类 → 结果 |

### 请求模型

```python
class TerminologyCheckRequest(BaseModel):
    file_path: str
    paragraph_indices: Optional[list[int]] = None

class TerminologyApplyRequest(BaseModel):
    file_path: str
    changes: list[ApplyChangeItem]  # ApplyChangeItem 定义于 base_paragraph_check.py
    operation_id: Optional[str] = None  # 检查操作 ID，用于查找 marker 映射
```
