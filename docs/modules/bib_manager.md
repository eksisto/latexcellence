# 参考文献管理模块 `bib_manager/`

## 设计概述

帮助用户自动识别参考文献格式、提取结构化元数据并生成规范的 BibTeX 代码，支持写入项目中的 `.bib` 文件。格式配置可保存为 JSON 文件复用。

### 核心流程

1. **格式识别**：用户提供参考文献示例 → LLM 分析格式类型、条目类型、字段列表 + 推荐 LaTeX 样式
2. **元数据提取**：用户输入文本或上传截图 → LLM 根据格式字段列表动态提取
  - **DOI 查询**：用户输入 DOI → 多数据源聚合查询元数据（Crossref 主索引 + 多个补全层 + arXiv 预印本专用路由）
3. **BibTeX 生成**：将元数据转换为标准 BibTeX 代码
4. **应用到文件**：选择 `.bib` 文件，追加或更新条目
5. **生成 LaTeX 代码**：生成包含正确引用的 LaTeX 代码片段

## 目录结构

```
bib_manager/
├── __init__.py
├── models.py                # Pydantic 请求/响应模型
├── router.py                # FastAPI 路由
├── service.py               # 业务逻辑编排
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

- `ReferenceFormatResult`（format_name, confidence, entry_type, required_fields, optional_fields, field_descriptions, latex_backend, latex_package 等）
- `ExtractedMetadata`（fields: dict — 动态字段，非写死）
- `BibEntry`（cite_key, entry_type, fields, raw_bibtex）
- `LatexCodeResult`（preamble, bibliographystyle, bibliography, cite_command, full_code）
- `DoiLookupResult`（title, authors, year, journal, volume, issue, pages, doi, publisher, abstract, url, isbn, issn, keywords, data_source — 标识数据来源）
- `DoiLookupRequest`（doi: str）
- 各类请求体：`FormatAnalysisRequest`、`ExtractRequest`、`GenerateBibtexRequest`、`SaveBibRequest`

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
       │     ├─ 成功 → 返回
       │     └─ 失败 ↓
       └─ 2. DataCite → OpenAlex → Europe PMC → Semantic Scholar（串行降级）
```

- arXiv DOI 由 `is_arxiv_doi()` / `extract_arxiv_id()` 工具函数识别
- 普通 DOI 优先查询 Crossref 获取最完整元数据；失败后按配置优先级串行尝试补全层
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

## Service 核心方法

| 方法 | 功能 |
| --- | --- |
| `analyze_format(example)` | LLM 识别参考文献格式 → ReferenceFormatResult |
| `extract_from_text(text, format_info)` | LLM 从文本提取元数据 |
| `extract_from_image(image_data, content_type, format_info)` | LLM Vision 从截图提取元数据 |
| `generate_bibtex(metadata, format_info, existing_keys)` | LLM 生成 BibTeX → BibEntry |
| `generate_latex_code(metadata, format_info, bib_file_path)` | 生成 LaTeX 代码片段 |
| `lookup_doi(doi)` | 委托 ProviderManager 查询多数据源聚合元数据 |
| `generate_bibtex_from_doi(doi, format_info, existing_keys)` | 查询 DOI + 生成 BibTeX 一步完成 |
| `read_bib_file(file_path)` | 读取 .bib 文件 |
| `save_bib_entry(file_path, bibtex_code, cite_key)` | 写入 .bib（已存在则替换） |
| `list_bib_files` | 列出项目中所有 .bib 文件 |
| `list_formats` / `load_format` / `save_format` / `delete_format` | 格式配置 CRUD |

## 样式注册表 `style_registry.py`

内置常见学术格式到 LaTeX 样式的映射（如 GB/T 7714-2015 → gbt7714、APA 7th → biblatex-apa、IEEE → IEEEtran 等）。`analyze_format()` 在 LLM 返回后自动补全缺失的 `latex_bibliographystyle`。

## API 路由

Prefix: `/api/bib-manager`

| 方法 | 路径 | 功能 |
| --- | --- | --- |
| POST | `/analyze-format` | 分析参考文献格式 |
| POST | `/extract` | 提取元数据（FormData） |
| POST | `/generate` | 生成 BibTeX |
| POST | `/generate-latex` | 生成 LaTeX 代码 |
| POST | `/doi-lookup` | 通过 DOI 查询多数据源聚合元数据 |
| POST | `/generate-from-doi` | 查询 DOI 并生成 BibTeX |
| GET | `/latex-styles` | 列出内置 LaTeX 样式映射 |
| POST | `/read-bib` | 读取 .bib 文件 |
| POST | `/save` | 写入 .bib 文件 |
| GET | `/list-bib-files` | 列出 .bib 文件 |
| GET/POST/DELETE | `/formats[/{name}]` | 格式配置 CRUD |

## Provider 配置

在 `config.json` 的 `bib_manager.providers` 节点中，可为每个数据源独立开关：

```json
{
  "bib_manager": {
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
```

默认启用 crossref、arxiv、datacite；openalex、europepmc、semantic_scholar 默认关闭（无认证即可使用但仍需谨慎对待 API 限流）。
