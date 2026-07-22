# 参考文献管理模块 `bib_manager/`

## 设计概述

参考文献管理帮助用户自动识别参考文献格式、提取结构化元数据并生成规范的 BibTeX 代码，支持写入项目中的 `.bib` 文件。格式配置可保存为 JSON 文件复用，避免重复识别。

### 核心流程（5 步向导）

1. **格式识别**：用户提供一条参考文献示例 → LLM 分析格式类型、置信度、条目类型、必填/可选字段 + **推荐 LaTeX 样式** → 可编辑/保存/加载格式
2. **元数据提取**：用户输入文本或上传截图 → LLM 根据格式的字段列表动态提取（字段名非写死）→ 可编辑动态表单 → 缺失字段（限于输入中实际出现的）告警
3. **BibTeX 生成**：将元数据转换为标准 BibTeX 代码，自动生成 cite_key（可编辑）
4. **应用到文件**：选择项目中的 `.bib` 文件，追加或更新条目
5. **生成 LaTeX 代码**：根据识别的格式，生成包含正确 `\bibliographystyle{}` 和 `\bibliography{}` 命令的 LaTeX 代码片段

### 与逐段检查模块的差异

| 维度 | typo_check / expression_opt | bib_manager |
| ---- | -------------------------- | ------------ |
| 交互模式 | 逐段 + SSE 流式 | 四步向导、逐步提交 |
| LLM 输出 | 段落级结构化结果 | 分步：格式分析 / 元数据提取 / BibTeX 生成 |
| Marker 保护 | 必须使用 | 不涉及（不操作 LaTeX 段落） |
| 元数据字段 | N/A | 动态：由格式的 required_fields / optional_fields 决定 |
| 前端模式 | `useParagraphCheck` hook | 独立页面，组件内 `useState` |

## `models.py` — Pydantic 模型

### 请求体

```python
class FormatAnalysisRequest(BaseModel):  # 格式分析
    example: str

class ExtractRequest(BaseModel):         # 元数据提取
    text: Optional[str] = None
    image_base64: Optional[str] = None
    format_info: Optional[dict] = None

class GenerateBibtexRequest(BaseModel):  # BibTeX 生成
    metadata: dict
    format_info: Optional[dict] = None
    existing_keys: list[str] = []

class GenerateLatexRequest(BaseModel):   # LaTeX 代码生成（新增）
    metadata: dict
    format_info: Optional[dict] = None
    bib_file_path: Optional[str] = None

class SaveBibRequest(BaseModel):         # 写入 .bib
    file_path: str
    bibtex_code: str
    cite_key: str
```

### LLM 输出 schema

```python
class ReferenceFormatResult(BaseModel):
    format_name: str                    # 格式名称（如 GB/T 7714-2015）
    confidence: str = "high"            # 置信度：high / medium / low
    entry_type: str                     # BibTeX 类型
    document_type_marker: Optional[str] # GB/T 的 [J]、[M] 等标识
    required_fields: list[str]
    optional_fields: list[str]
    field_descriptions: dict[str, str]  # 字段中文说明
    format_description: str             # 格式规则描述
    raw_observed_fields: list[str]      # 输入中实际出现的字段列表
    # LaTeX 样式字段（由 LLM 推荐 + style_registry 补全）
    latex_backend: Optional[str]        # "bibtex" 或 "biblatex"
    latex_package: Optional[str]        # 需要的 LaTeX 宏包
    latex_bibliographystyle: Optional[str]  # .bst 样式名
    latex_preamble: Optional[str]       # 完整 preamble 代码片段

class ExtractedMetadata(BaseModel):     # 动态字段，非写死
    fields: dict[str, str] = {}         # key 由格式的 required_fields 等决定

class BibEntry(BaseModel):
    cite_key: str
    entry_type: str
    fields: dict[str, str] = {}
    raw_bibtex: str = ""

class LatexCodeResult(BaseModel):       # LaTeX 代码输出（新增）
    preamble: str = ""                  # 导言区宏包配置
    bibliographystyle: str = ""         # \bibliographystyle{...} 命令
    bibliography: str = ""              # \bibliography{...} 命令
    cite_command: str = ""              # \cite{...} 引用示例
    full_code: str = ""                 # 拼好的完整代码片段
```

## `service.py` — 核心业务逻辑

**单例**：`BibManagerService` 通过模块级 `_service` 懒加载。

| 方法 | 说明 |
| ---- | ---- |
| `analyze_format(example)` | LLM 识别参考文献格式 → `ReferenceFormatResult`，温度 0.2；Prompt 自包含示例（`<reference>` 标签）；若 LLM 未提供 LaTeX 样式字段，自动从 `style_registry` 补全 |
| `extract_from_text(text, format_info)` | LLM 从文本提取元数据 → `ExtractedMetadata`，字段名由 format_info 动态指定，温度 0.1 |
| `extract_from_image(image_data, content_type, format_info)` | LLM Vision 从截图提取元数据，温度 0.1，预处理复用 `formula_ocr` 的图片缩放/base64 模式 |
| `generate_bibtex(metadata, format_info, existing_keys)` | LLM 生成 BibTeX → `BibEntry`，温度 0.1 |
| `generate_latex_code(metadata, format_info, bib_file_path)` | 根据格式信息生成包含正确样式的 LaTeX 代码片段 → `LatexCodeResult`（新增） |
| `read_bib_file(file_path)` | 通过 `safe_read` 读取项目中的 `.bib` 文件 |
| `save_bib_entry(file_path, bibtex_code, cite_key)` | 调用 `atomic_write` 写入 `.bib` 文件；若 cite_key 已存在则替换原条目 |
| `list_bib_files()` | 通过 `rglob("*.bib")` 列出项目中所有 `.bib` 文件 |
| `list_formats()` | 列出 `formats/` 目录下保存的格式 JSON 文件 |
| `load_format(name)` | 加载指定格式配置 |
| `save_format(name, format_info)` | 将格式配置保存为 JSON 文件（文件名由 format_name 自动生成） |
| `delete_format(name)` | 删除指定格式配置 |

### LLM Prompt 设计

- **格式分析 Prompt**：结构化 `## Input / ## Task / ## Guidelines / ## Output`，`<reference>` 标签包裹输入；要求输出 `confidence` 和 `raw_observed_fields`；置信度 `"low"` 时前端弹出 Toast 警告。Prompt 中包含常见格式到 LaTeX 样式的映射提示，要求 LLM 同时输出 `latex_backend`、`latex_package`、`latex_bibliographystyle`、`latex_preamble` 四个字段
- **元数据提取 Prompt**：输出格式为 `{"fields": {"author": "...", "journal": "..."}}`，字段名由 format_info.required_fields + optional_fields 动态指定，不绑定固定字段名
- **BibTeX 生成 Prompt**：自动生成 cite_key（`{作者姓}{年份}{关键词}` 风格），字段用花括号包裹，保留 LaTeX 特殊字符

### 缺失字段检测

`_check_missing_fields(fields, format_info)` 只报告 **同时出现在 `raw_observed_fields` 中** 但值为空的必填字段。如果输入中本就没有某个字段（如 abstract），则不视为缺失。

### 格式配置持久化

格式 JSON 存储于 `formats/` 目录，文件名由 `format_name` 中的非法字符替换为 `-` 后生成。支持 CRUD，前端下拉列表可一键加载已保存格式，跳过 LLM 分析。

## `style_registry.py` — LaTeX 样式注册表（新增）

内置常见学术引用格式到 LaTeX 排版样式的映射：

| 格式 | LaTeX 后端 | 宏包 | .bst / style | 说明 |
| ---- | ---------- | ---- | ------------ | ---- |
| GB/T 7714-2015 | BibTeX | `gbt7714` | `gbt7714-numerical` | 中国国标（顺序编码制） |
| APA 7th | biblatex | `biblatex` | `style=apa` | APA 第7版（作者-年份制） |
| IEEE | BibTeX | `IEEEtran` | `IEEEtran` | IEEE 期刊/会议格式 |
| MLA 9th | BibTeX | `mla13` | `mla` | MLA 第9版 |
| Chicago | BibTeX | `chicago` | `chicago` | 芝加哥手册格式 |
| Vancouver | BibTeX | — | `vancouver` | 温哥华格式（医学期刊） |

### 查询函数

- `get_style_for_format(format_name)`：先精确匹配 key，再遍历别名做包含匹配
- `list_all_styles()`：返回所有内置样式映射

### 补全逻辑

`analyze_format()` 在 LLM 返回结果后，若 `latex_bibliographystyle` 为空，自动调用 `get_style_for_format()` 从注册表补全。

## `router.py` — API 路由

路由前缀：`/api/bib-manager`

| 端点 | 方法 | 说明 |
| ---- | ---- | ---- |
| `/analyze-format` | POST | 分析参考文献格式 |
| `/extract` | POST | 提取元数据（FormData：text + image + format_info） |
| `/generate` | POST | 生成 BibTeX 代码 |
| `/generate-latex` | POST | 生成包含正确样式的 LaTeX 代码（新增） |
| `/latex-styles` | GET | 列出所有内置 LaTeX 样式映射（新增） |
| `/read-bib` | POST | 读取 .bib 文件内容 |
| `/save` | POST | 追加/更新条目到 .bib 文件 |
| `/list-bib-files` | GET | 列出项目中所有 .bib 文件 |
| `/formats` | GET | 列出已保存格式配置 |
| `/formats/{name}` | GET | 加载指定格式 |
| `/formats` | POST | 保存格式配置 |
| `/formats/{name}` | DELETE | 删除指定格式 |

## 前端页面 `BibManagerPage.jsx`

五步向导 UI，状态管理使用组件内 `useState`，不依赖 Zustand slice：

1. **格式识别**：Textarea 输入示例 → 识别按钮 → 显示格式信息卡片（格式名、entry_type、置信度、必填/可选字段标签、格式描述）→ **显示推荐 LaTeX 样式**（`\bibliographystyle{...}` 命令和所需宏包）；"编辑格式"按钮可修改格式名称、entry_type、字段列表；"保存格式"按钮自动从 `format_name` 生成文件名并持久化；"加载已保存格式"下拉框可一键跳过 LLM 分析
2. **元数据提取**：Textarea 文本或图片上传（FormData） → 提取按钮 → 动态可编辑表单（字段由 `result.fields` 的 key 决定，label 优先取 `field_descriptions[key]`）+ 缺失字段标红高亮 → Toast 每项缺失警告
3. **BibTeX 生成**：生成按钮 → 可编辑的 BibTeX 代码区 + cite_key 输入框 → 复制按钮
4. **应用到文件**：Select 下拉选择 `.bib` 文件 → 应用按钮 → 写入确认
5. **生成 LaTeX 代码**：输入 .bib 文件路径 → 生成按钮 → 显示完整 LaTeX 代码片段（含 `\bibliographystyle`、`\bibliography`、`\cite` 示例）→ 复制按钮

### 核心交互

- 置信度 `"low"` 时弹出 `toast.warning` 提示用户检查结果
- 元数据缺失字段逐项 `toast.warning` 警告
- 格式编辑后"确认修改"更新格式结果，"取消"恢复原状
- 保存格式自动刷新下拉列表
