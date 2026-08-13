# 自动编译模块 `compile/`

通过 `subprocess` 调用 `latexmk` 编译主文件，支持自动引擎检测和 PDF/A 归档输出。编译失败时使用 LLM 翻译 stderr 错误信息。不使用 Marker 保护策略。

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

## Pydantic 输出模型

- `CompileResult`（success, stdout, stderr, explanation, fixes）
- `ErrorTranslation`（explanation, fixes）

## Service 核心方法

| 方法 | 功能 |
| --- | --- |
| `compile_latex(main_file, workspace_id, engine, pdfa)` | 调用 latexmk → 捕获 stderr → 失败时 LLM 翻译 → 返回 CompileResult |
| `_extract_key_errors(stderr_text)` | 从 stderr 提取关键错误行 |

## API 路由

Prefix: `/api/compile`, Tag: `compile`

| 方法 | 路径 | 功能 |
| --- | --- | --- |
| POST | `/run` | 编译 LaTeX 项目（支持 engine/pdfa 参数） |
| GET | `/pdf` | 返回最近一次编译产出的 PDF（内联预览） |

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
