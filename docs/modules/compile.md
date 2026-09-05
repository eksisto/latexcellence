# 自动编译模块 `compile/`

通过 `subprocess` 调用 `latexmk` 编译主文件，支持自动引擎检测和 PDF/A 归档输出。编译失败时使用 LLM 翻译 stderr 错误信息。不使用 Marker 保护策略。另提供 SyncTeX 正/反向只读查询端点（editor v3 阶段一），供编辑器 PDF ↔ 源码互跳。

| 维度 | 编译模块 | 其他 LLM 模块 |
| --- | --- | --- |
| LLM 角色 | 仅翻译错误信息 | 修改/优化文本 |
| 是否修改源文件 | 否（生成 PDF） | 是 |

## 核心功能

### 引擎自动检测

根据 `.tex` 文件内容自动选择编译引擎：

1. 检测 XeLaTeX 专属宏包（fontspec/xeCJK/polyglossia）→ xelatex
2. 检测 LuaLaTeX 专属宏包/命令（luacode/luatextra/directlua）→ lualatex
3. 检测非 ASCII 字符（中文/日文/韩文等）→ xelatex（pdfLaTeX 不支持 Unicode）
4. 以上都不匹配 → pdflatex

### PDF/A 归档输出

在 `\documentclass` 之后注入 `\usepackage[a-2b]{pdfx}` 以支持 PDF/A-2b 归档格式输出。若已存在 `pdfx` 或 `pdfmanagement-testphase` 包则跳过注入。

### SyncTeX 数据与正/反向查询（editor v3 阶段一）

- `CompileRequest` / `CompileResult` 携带可选 `synctex` 字段（默认
  false，编译页 / git_diff 等既有调用方零影响）：请求为 true 且未与
  PDF/A 组合时，`run_tex_compile()` 在 latexmk 基础参数追加
  `-synctex=1`（与 extra_args 同级）；PDF/A 在临时目录编译后仅回拷
  PDF，`.synctex.gz` 不同步回项目目录，故组合时忽略并在响应如实回传
  `synctex=false`。
- `POST /api/compile/synctex/forward` / `POST /api/compile/synctex/backward`
  为**零写盘只读查询**：`synctex view` / `synctex edit`（TeX Live 自带）
  经 SandboxRunner（`ExecSpec(image_kind="tex", cwd=项目根)`）执行；
  `file_path` 经 `resolve_project_path()` 白名单校验（`.tex/.bib/.sty/.cls`，
  与编辑器可编辑白名单同口径）；synctex 输出中的绝对路径在多用户模式下
  经 `sanitize_output_paths()` 脱敏，响应只含项目内相对路径，定位到项目
  外文件（texmf 系统文件等）按未命中处理。

## Pydantic 输出模型

- `CompileResult`（success, stdout, stderr, explanation, fixes, error_code, synctex）
- `ErrorTranslation`（explanation, fixes）
- `SynctexForwardResponse`（records: page/x/y/w/h，72dpi 点单位、页面左上原点）
- `SynctexBackwardResponse`（found, file_path, line, col；未命中 found=false 属合法空结果）

## Service 核心方法

| 方法 | 功能 |
| --- | --- |
| `compile_latex(main_file, workspace_id, language, engine, pdfa, synctex)` | 调用 latexmk → 捕获 stderr → 失败时 LLM 翻译 → 返回 CompileResult |
| `_extract_key_errors(stderr_text)` | 从 stderr 提取关键错误行 |
| `synctex_forward(workspace_id, main_file, file_path, line, col)` | 正向：`synctex view -i "line:col:file" -o pdf`，解析输出中的页面矩形记录 |
| `synctex_backward(workspace_id, main_file, page, x, y)` | 反向：`synctex edit -o "page:x:y:pdf"`，Input 路径归一为项目内相对路径 |
| `parse_synctex_view_records(output)` / `parse_synctex_edit_result(output)` | synctex CLI 输出解析纯函数（保守解析，畸形记录丢弃） |

## API 路由

Prefix: `/api/compile`, Tag: `compile`

| 方法 | 路径 | 功能 |
| --- | --- | --- |
| POST | `/run` | 编译 LaTeX 项目（支持 engine/pdfa/synctex 参数） |
| GET | `/pdf` | 返回最近一次编译产出的 PDF（内联预览） |
| POST | `/synctex/forward` | 正向同步：源码位置 → PDF 页面矩形（只读零写盘） |
| POST | `/synctex/backward` | 反向同步：PDF 点位 → 源码位置（相对路径；未命中 found=false） |

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
