# 自动编译模块 `compile/`

经 `SandboxRunner` 执行 `texfot` 包装的 `latexmk` 编译主文件（docker tex 镜像 / local 回落），支持自动引擎检测、PDF/A 归档输出与编译前产物清理。编译失败时使用 LLM 翻译 stderr 错误信息。不使用 Marker 保护策略。另提供 SyncTeX 正/反向只读查询端点（editor v3 阶段一），供编辑器 PDF ↔ 源码互跳。

| 维度 | 编译模块 | 其他 LLM 模块 |
| --- | --- | --- |
| LLM 角色 | 仅翻译错误信息 | 修改/优化文本 |
| 是否修改源文件 | 否（生成 PDF） | 是 |

## 核心功能

### 引擎自动检测

根据 `.tex` 文件内容自动选择编译引擎：

1. 检测 XeLaTeX 专属宏包/命令（fontspec/xeCJK/polyglossia/unicode-math、`\setmainfont` 等字体命令）与 ctex 文档类 → xelatex
2. 检测 LuaLaTeX 专属宏包/命令（luacode/luatextra/directlua/luatexja 等）→ lualatex
3. 检测非 ASCII 字符（中文/日文/韩文等）→ xelatex（pdfLaTeX 不支持 Unicode）
4. 以上都不匹配 → pdflatex

### PDF/A 归档输出

在 `\documentclass` 之后注入 `\usepackage[a-2b]{pdfx}` 以支持 PDF/A-2b 归档格式输出。若已存在 `pdfx` 或 `pdfmanagement-testphase` 包则跳过注入。

### 编译前产物清理（clean_first）

- 动机：latexmk 依赖项目内持久化的 `.fdb_latexmk` / `.log` 记录上次运行状态。源文件未变化时短路为 "Nothing to do"，但因上次失败的错误状态仍会以 exit 12 失败。其关键线索行（"gave an error in previous invocation"）又不命中 texfot 的 stdout 过滤模式，用户只能看到无信息量的 `-f` 提示。
- `CompileRequest.clean` / `compile_latex(clean_first=...)`：为 true 时编译前调用 `clean_project_artifacts(root)` 清理项目内可再生产物后再执行 latexmk，保证引擎必跑、失败日志始终来自本次运行。编辑器编译链路恒传 true。编译页 / git_diff 等既有调用方默认 false，行为零变化。
- `clean_project_artifacts(root)` 遍历项目根目录（含 `\input` 子目录），按扩展名白名单删除（latexmk 默认清理集 + 常见宏包/工具产物，含 `.synctex.gz` / `.run.xml` 等复合后缀）。跳过 `.git` 目录、符号链接与非普通文件。`.pdf` 与 `.tex/.bib/.sty` 等源文件不在白名单，永不删除。
  同步阻塞 I/O，调用方经 `asyncio.to_thread` 调度。

### SyncTeX 数据与正/反向查询（editor v3 阶段一）

- `CompileRequest` / `CompileResult` 携带可选 `synctex` 字段（默认 false，编译页 / git_diff 等既有调用方零影响）：请求为 true 且未与 PDF/A 组合时，`run_tex_compile()` 在 latexmk 基础参数追加 `-synctex=1`（与 extra_args 同级）。PDF/A 在临时目录编译后仅回拷 PDF，`.synctex.gz` 不同步回项目目录，故组合时忽略并在响应如实回传 `synctex=false`。
- `POST /api/compile/synctex/forward` / `POST /api/compile/synctex/backward` 为**零写盘只读查询**：`synctex view` / `synctex edit`（TeX Live 自带）经 SandboxRunner（`ExecSpec(image_kind="tex", cwd=项目根)`）执行。`file_path` 经 `resolve_project_path()` 白名单校验（`.tex/.bib/.sty/.cls`，与编辑器可编辑白名单同口径）。synctex 输出中的绝对路径在多用户模式下经 `sanitize_output_paths()` 脱敏，响应只含项目内相对路径，定位到项目外文件（texmf 系统文件等）按未命中处理。

## Pydantic 输出模型

- `CompileResult`（success, stdout, stderr, explanation, fixes, error_code, synctex）
- 沙箱内存超限（OOM）时返回结构化错误：`error_code="sandbox_killed_oom"` + 固定建议，不走 LLM 翻译
- `ErrorTranslation`（explanation, fixes）
- `SynctexForwardResponse`（records: page/x/y/w/h，72dpi 点单位、页面左上原点）
- `SynctexBackwardResponse`（found, file_path, line, col。未命中 found=false 属合法空结果）

## Service 核心方法

| 方法 | 功能 |
| --- | --- |
| `compile_latex(main_file, workspace_id, language, engine, pdfa, synctex, clean_first)` | 可选清理再生产物 → 调用 latexmk → 捕获 stderr → 失败时 LLM 翻译 → 返回 CompileResult |
| `clean_project_artifacts(root)` | 按扩展名白名单清理项目内可再生产物（跳过 .git / 符号链接 / PDF / 源文件），返回删除文件数 |
| `translate_compile_errors`（`backend/utils/compile_error_translator.py` 共享函数） | 从编译日志提取关键错误行（`extract_key_errors`），经 LLM 翻译为中文解释与修复建议 |
| `synctex_forward(workspace_id, main_file, file_path, line, col)` | 正向：`synctex view -i "line:col:file" -o pdf`，解析输出中的页面矩形记录 |
| `synctex_backward(workspace_id, main_file, page, x, y)` | 反向：`synctex edit -o "page:x:y:pdf"`，Input 路径归一为项目内相对路径 |
| `parse_synctex_view_records(output)` / `parse_synctex_edit_result(output)` | synctex CLI 输出解析纯函数（保守解析，畸形记录丢弃） |

## API 路由

Prefix: `/api/compile`, Tag: `compile`

| 方法 | 路径 | 功能 |
| --- | --- | --- |
| POST | `/run` | 编译 LaTeX 项目（支持 engine/pdfa/synctex/clean 参数） |
| GET | `/pdf` | 返回最近一次编译产出的 PDF（内联预览） |
| POST | `/synctex/forward` | 正向同步：源码位置 → PDF 页面矩形（只读零写盘） |
| POST | `/synctex/backward` | 反向同步：PDF 点位 → 源码位置（相对路径，未命中 found=false） |

## 配置项

```json
{
  "compile": {
    "enabled": true,
    "latexmk_timeout": 120,
    "engine": "auto",
    "pdfa": false
  }
}
```

| 字段 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| enabled | bool | true | 是否启用编译模块 |
| latexmk_timeout | int | 120 | latexmk 编译超时时间（秒） |
| engine | str | "auto" | 编译引擎：auto/pdflatex/xelatex/lualatex |
| pdfa | bool | false | 是否启用 PDF/A-2b 归档格式输出 |
