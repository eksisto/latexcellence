# 参考文献管理模块 `bib_manager/`

## 设计概述

自动识别参考文献格式、提取结构化元数据并生成规范的 BibTeX 代码，支持写入项目中的 `.bib` 文件。格式配置可保存为 JSON 文件复用。

### 核心流程

1. **格式识别**：用户提供参考文献示例 → 确定性规则层优先判定（文献类型标识、期刊缩写、DOI 前缀、版式特征），未命中时由 LLM 兜底（提示词内置各格式规范样例）。LaTeX 样式由后端注册表统一解析
2. **元数据提取**：用户输入文本或上传截图 → LLM 根据格式字段列表动态提取

- **DOI 查询**：用户输入 DOI → 多数据源聚合查询元数据（Crossref 主索引 + 多个补全层 + arXiv 预印本专用路由）

3. **BibTeX 生成**：将元数据转换为标准 BibTeX 代码
4. **应用到文件**：选择 `.bib` 文件，追加或更新条目
5. **生成 LaTeX 代码**：生成包含正确引用的 LaTeX 代码片段

写入 `.bib` 统一走 `LatexProject.apply_whole_files()` 整文件事务：写盘前推入撤销栈，因此保存、更新、删除条目都可以在 workspace 页对该文件执行撤销/重做。写回同时携带读取时计算的 MD5 作为乐观锁，文件在读取后被其他工作区修改时抛 `FileConflictError`，由路由转为 HTTP 409。

## 目录结构

```
bib_manager/
├── __init__.py
├── models.py                # Pydantic 请求/响应模型
├── router.py                # FastAPI 路由
├── service.py               # 业务逻辑编排
├── bib_parser.py            # BibTeX 解析器（条目解析、规范化、重复检测工具函数）
├── format_rules.py          # 格式确定性识别规则（规则优先，LLM 兜底）
├── style_registry.py        # 内置样式映射注册表
├── provider_manager.py      # DOI 数据源管理器（全局单例）
└── providers/               # 可插拔数据源提供者
    ├── __init__.py
    ├── base.py              # BaseMetadataProvider 抽象基类 + DOI 工具函数
    ├── crossref.py          # Crossref API（主索引）
    ├── arxiv.py             # arXiv API（预印本专用）
    ├── datacite.py          # DataCite API（补全层）
    ├── openalex.py          # OpenAlex API（补全层）
    ├── europepmc.py         # Europe PMC API（补全层）
    └── semantic.py          # Semantic Scholar API（补全层）
```

## Pydantic 模型

- `ReferenceFormatResult`（format_name, confidence, entry_type, required_fields, optional_fields, field_descriptions, raw_observed_fields 等）。latex_* 样式字段由 service 按注册表填充到响应，不属于 LLM 输出。
- `SavedFormatInfo`（用户保存/自定义格式配置：格式字段 + 可选 latex_* 样式字段）。保存时统一校验 backend 枚举、宏包/样式名和导言区 LaTeX 内容。
- `ExtractedMetadata`（fields: dict — 动态字段，非写死）
- `BibEntry`（cite_key, entry_type, fields, raw_bibtex）
- `LatexCodeResult`（preamble, bibliographystyle, bibliography, cite_command, full_code）
- `DoiLookupResult`（title, authors, year, journal, volume, issue, pages, doi, publisher, abstract, url, isbn, issn, keywords, data_source — 标识数据来源）
- `DoiLookupRequest`（doi: str）
- 各类请求体：`FormatAnalysisRequest`、`ExtractRequest`、`GenerateBibtexRequest`、`GenerateLatexRequest`（含 `language`，用于指定生成代码注释的 UI 语言）、`SaveBibRequest`
- **文献库管理模型**：`ParseBibRequest`、`ParseBibResponse`、`DetectDuplicatesRequest`、`DetectDuplicatesResponse`、`DuplicateGroup`、`DeleteEntryRequest`、`UpdateEntryRequest`、`EntryStatsResponse`
- `BibEntry.fields` / `raw_bibtex` 经 `latex_json_guard.py` 内容校验（BibTeX 语义：`%` 为字面字符），拦截 LLM JSON 输出中的反斜杠转义损坏。`latex_preamble` 由注册表提供，用户保存的自定义样式在写入前经 `SavedFormatInfo` 校验

## 多数据源架构（DOI 查询）

### 设计思想

参考 `web_search/` 多引擎抽象模式，采用**可插拔数据源提供者**架构：

- `BaseMetadataProvider` 抽象基类定义 `lookup_by_doi()` / `health_check()` 接口
- 每个数据源是一个独立的 Provider 类，以标准化输出转换为 `DoiLookupResult`
- `ProviderManager` 统一管理注册、优先级和降级

### 查询策略

```
输入 DOI
  ├─ 10.48550/arXiv.* → ArxivProvider（提取 arXiv ID，调用 arXiv API）
  └─ 普通 DOI
       ├─ 1. Crossref（主索引）
       │     ├─ 成功 → 用补全层合并空字段（_merge）后返回
       │     └─ 失败 ↓
       └─ 2. DataCite → OpenAlex → Europe PMC → Semantic Scholar（串行降级）
```

- arXiv DOI 由 `is_arxiv_doi()` / `extract_arxiv_id()` 工具函数识别
- 普通 DOI 优先查询 Crossref 获取最完整元数据，成功后用补全层填充缺失字段（`_merge`）。失败后按配置优先级串行尝试补全层
- 所有 Provider 返回 `None` 时抛出 `ValueError("All providers failed")`

### 数据源列表

| Provider | name | API 端点 | 用途 |
|----------|------|----------|------|
| Crossref | crossref | `api.crossref.org/works/{doi}` | 主索引（最完整） |
| arXiv | arxiv | `export.arxiv.org/api/query?id_list={id}` | 预印本专用 |
| DataCite | datacite | `api.datacite.org/dois/{doi}` | 补全层 |
| OpenAlex | openalex | `api.openalex.org/works/doi:{doi}` | 补全层 |
| Europe PMC | europe_pmc | `ebi.ac.uk/europepmc/webservices/rest/search` | 补全层 |
| Semantic Scholar | semantic_scholar | `api.semanticscholar.org/graph/v1/paper/` | 补全层 |

### ProviderManager 全局单例

`get_provider_manager()` 按配置注册 Provider：

- `providers.crossref` → 注册为**主索引**
- `providers.arxiv` → 注册为 **arXiv 专用路由**
- 其他 Provider → 注册为**补全层**（按代码中注册顺序降级）

配置变更时需要调用 `reset_provider_manager()` 重建单例。

各 Provider 持有进程级懒创建的共享 `httpx.AsyncClient`（连接复用、按请求传超时），服务关闭时由 `close_provider_manager()` 统一释放。

## Service 核心方法

| 方法 | 功能 |
| --- | --- |
| `analyze_format(example)` | 格式识别：确定性规则层优先，未命中时 LLM 兜底 → ReferenceFormatResult |
| `extract_from_text(text, format_info)` | LLM 从文本提取元数据 |
| `extract_from_images(image_items, format_info)` | LLM Vision 批量提取多张截图元数据（`/extract` 端点接受多文件上传）；`extract_from_image` 为单图包装 |
| `generate_bibtex(metadata, format_info, existing_keys)` | LLM 生成 BibTeX → BibEntry |
| `generate_latex_code(metadata, format_info, bib_file_path, language)` | 生成 LaTeX 代码片段（注释跟随 UI 语言） |
| `lookup_doi(doi)` | 委托 ProviderManager 查询多数据源聚合元数据 |
| `generate_bibtex_from_doi(doi, format_info, existing_keys)` | 查询 DOI + 生成 BibTeX 一步完成 |
| `read_bib_file(file_path)` | 读取 .bib 文件 |
| `save_bib_entry(file_path, bibtex_code, cite_key)` | 写入 .bib（已存在则替换） |
| `list_bib_files` | 列出项目中所有 .bib 文件 |
| `list_formats` / `load_format` / `save_format` / `delete_format` | 格式配置 CRUD |
| `parse_entries(file_path, workspace_id)` | 解析 .bib 文件为结构化条目列表 |
| `detect_duplicates(file_path, workspace_id)` | 检测 .bib 文件中的重复条目（多策略：DOI/标题相似度/cite_key） |
| `delete_entry(file_path, cite_key, workspace_id)` | 从 .bib 文件中删除指定条目 |
| `update_entry(file_path, cite_key, bibtex_code, workspace_id)` | 更新 .bib 文件中的指定条目 |
| `get_entry_stats(file_path, workspace_id)` | 获取 .bib 文件的条目统计信息（按类型/年份/DOI 覆盖率） |

## 格式识别规则层 `format_rules.py`

格式识别优先使用确定性规则，覆盖多种内置格式（GB/T 7714-2015 的
`[J]`/`[M]`/`[C]` 文献类型标识、`Phys. Rev. Lett.` → APS、`J. Am. Chem. Soc.`
→ ACS、`JAMA`/`10.1001` → AMA、`J. ACM`/`10.1145` → ACM、`Nature 卷, 页` →
Nature、`vol./no./pp.` → IEEE、MLA 的 `卷, 期, 年, pp.`、温哥华的
`年;卷(期):页码`、Harvard 的 `Last, I. and Last, I., YYYY.`、Chicago 著者-出版年、
Turabian/Chicago notes 的 `卷, no. 期 (年): 页码`、APA 的 `(年份).` 等）。

规则命中时直接返回 `confidence=high`，不调用 LLM、不依赖 API Key。未命中才走
LLM 兜底（提示词内置每种格式的规范样例供比对）。规则语料见
`tests/test_utils/test_bib_manager_format_rules.py`，新增格式必须同步补语料。

## 样式注册表 `style_registry.py`

内置常见学术格式到 LaTeX 样式的映射，当前覆盖：GB/T 7714-2015、APA 7th、IEEE、MLA 9th、Chicago、Vancouver、Harvard、Nature、AMA、ACS、OSCOLA、Turabian、ACM、APS。`analyze_format()` 识别出格式名后，一律按注册表解析并填充响应中的 `latex_*` 字段。LLM 输出 schema 不再包含样式字段（只负责识别格式），提示词与注册表通过测试保持同步，避免映射知识漂移。

注册表未命中的格式返回空样式字段，前端允许用户通过“编辑格式”填写自定义 LaTeX 样式并保存。`save_format()` 保存前经 `SavedFormatInfo` 校验（backend 仅允许 `bibtex`/`biblatex`，宏包与样式名不允许空格/花括号，导言区走 `latex_json_guard` 内容校验），不再原样写 JSON。加载旧配置时若缺少样式字段，会按格式名从注册表自动补全。格式名仅允许字母数字与 `_`/`-`，防止路径穿越。

## API 路由

Prefix: `/api/bib-manager`

| 方法 | 路径 | 功能 |
| --- | --- | --- |
| POST | `/analyze-format` | 分析参考文献格式 |
| POST | `/extract` | 提取元数据（FormData） |
| POST | `/generate` | 生成 BibTeX |
| POST | `/generate-latex` | 生成 LaTeX 代码（注释跟随 UI 语言） |
| POST | `/doi-lookup` | 通过 DOI 查询多数据源聚合元数据 |
| POST | `/generate-from-doi` | 查询 DOI 并生成 BibTeX |
| GET | `/latex-styles` | 列出内置 LaTeX 样式映射 |
| POST | `/read-bib` | 读取 .bib 文件 |
| POST | `/save` | 写入 .bib 文件 |
| GET | `/list-bib-files` | 列出 .bib 文件 |
| GET/POST/DELETE | `/formats[/{name}]` | 格式配置 CRUD |
| POST | `/parse-entries` | 解析 .bib 文件为结构化条目列表 |
| POST | `/detect-duplicates` | 检测 .bib 文件中的重复条目 |
| POST | `/delete-entry` | 从 .bib 文件中删除指定条目 |
| POST | `/update-entry` | 更新 .bib 文件中的指定条目 |
| GET | `/entry-stats` | 获取 .bib 文件的条目统计信息 |

## Provider 配置

在 `config.json` 的 `features.bib_manager` 节点中，可为每个数据源独立开关（`enabled` 控制整个参考文献管理模块，`format_configs_dir` 为格式配置文件目录）：

```json
{
  "features": {
    "bib_manager": {
      "enabled": true,
      "format_configs_dir": "data/formats/",
      "providers": {
        "crossref": { "enabled": true },
        "arxiv": { "enabled": true },
        "datacite": { "enabled": true },
        "openalex": { "enabled": false },
        "europepmc": { "enabled": false },
        "semantic_scholar": { "enabled": false }
      }
    }
  }
}
```

默认启用 crossref、arxiv、datacite。openalex、europepmc、semantic_scholar 默认关闭（无需认证，但需注意 API 限流）。
