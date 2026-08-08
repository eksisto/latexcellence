# 术语一致性检查模块 `terminology_check/`

使用两阶段 LLM 调用（提取 + 语义聚类），而非固定算法。术语一致性本质是语义问题——"Deep Learning"与"深度学习"在文本层面不同，但语义上是同一概念。

| 维度 | 术语检查 | typo_check / expression_opt |
| --- | --- | --- |
| LLM 调用次数 | N+1（N 段提取 + 1 次聚类） | N（逐段） |
| 跨段落分析 | 是（全文术语聚类） | 否（逐段独立） |
| 检测方式 | LLM 语义聚类 | LLM 逐段修改 |
| 修复粒度 | 术语替换 | 全段落替换 |
| 用户自定义 | 支持自定义替换 | 仅接受/忽略 |

## Pydantic 输出模型

- `ExtractedTerm` — 单条抽取术语（term, term_type, context）
- `TermExtractionResult` — 术语抽取响应（paragraph_index, terms）
- `TermChange` — 术语修改建议（type, concept, original, corrected, explanation）
- `TermCluster` — 语义聚类结果（recommended, variants）
- `TermClusterResult` — 聚类响应（clusters）
- `TermParagraphResult` — 逐段检查结果（paragraph_index, original, corrected, terms, changes）

## 术语提取与语义聚类 `extractor.py`

| 方法 | 功能 |
| --- | --- |
| `extract_terms(paragraph_index, plain_text)` | LLM 从单段提取关键术语 |
| `extract_terms_fallback(plain_text)` | 无 LLM 时的规则降级（基于大写缩写正则） |
| `cluster_terms(unique_terms)` | LLM 对去重后的术语语义聚类 |

## Service 三步走流程

```
Phase 1: 逐段提取
  按 batch_size 分批，每批 asyncio.gather 并发调用 LLM 提取术语
  所有术语收集到 all_terms 列表

Phase 2: 语义聚类
  _deduplicate_terms(all_terms) → unique_terms（精确匹配去重）
  extractor.cluster_terms(unique_terms) → clusters（LLM 语义分组）

Phase 3: 生成修正结果
  构建 replace_map: variant → recommended（排除推荐项自身）
  为每个段落找到需要替换的变体，生成 changes + corrected 文本
  按变体长度降序替换，防止短变体误匹配长变体子串
```

| 方法 | 功能 |
| --- | --- |
| `preview_paragraphs` | 提取段落 + 剥离 LaTeX + 缓存 marker_map |
| `check_terminology` | 三步走流程 |
| `check_terminology_stream` | SSE 流式推送 |
| `apply_changes` | 还原 marker + 原子写回 |
| `_deduplicate_terms` | 精确匹配去重，保持首次出现顺序 |

### 三层防护（与 typo_check 一致）

| 层级 | 防护内容 | 失败处理 |
| --- | --- | --- |
| 第 1 层 | `retry_with_validation()`（最多 3 次） | LLM 输出不符 schema 时反馈重试 |
| 第 2 层 | Marker 匹配验证 | 不一致时回退到原文 |
| 第 3 层 | 异常兜底 | LLM 调用异常时返回原文 + 空 changes |

## API 路由

Prefix: `/api/terminology-check`, Tag: `terminology_check`

| 方法 | 路径 | 功能 |
| --- | --- | --- |
| GET | `/preview` | 预览段落 |
| POST | `/check` | 逐段提取术语 + 语义聚类 |
| POST | `/apply` | 写回术语修改 |
| GET | `/stream/{workspace_id}` | SSE 流式推送 |

请求模型：`TerminologyCheckRequest`、`TerminologyApplyRequest`

## 人工评估

`tools/terminology_check_eval/` 提供术语一致性人工评估工具，配套语料位于
`tests/fixtures/terminology_check/`（每条一个多段落 LaTeX 文档 + 期望术语簇）。
新增语料或修改术语提取/聚类逻辑后，运行 `python tools/terminology_check_eval/run_eval.py`
生成人工比对报告；新增语料先跑 `--validate` 校验。详见其 `README.md`。
