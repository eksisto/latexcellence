# 自动编译模块 `compile/`

通过 `subprocess` 调用 `latexmk -xelatex` 编译主文件，仅在编译失败时使用 LLM 翻译 stderr 错误信息。不使用 Marker 保护策略。

| 维度 | 编译模块 | 其他 LLM 模块 |
| --- | --- | --- |
| LLM 角色 | 仅翻译错误信息 | 修改/优化文本 |
| 是否修改源文件 | 否（生成 PDF） | 是 |

## Pydantic 输出模型

- `CompileResult`（success, stdout, stderr, explanation, fixes）
- `ErrorTranslation`（explanation, fixes）

## Service 核心方法

| 方法 | 功能 |
| --- | --- |
| `compile_latex(main_file, workspace_id)` | 调用 latexmk → 捕获 stderr → 失败时 LLM 翻译 → 返回 CompileResult |
| `_extract_key_errors(stderr_text)` | 从 stderr 提取关键错误行 |

## API 路由

Prefix: `/api/compile`, Tag: `compile`

| 方法 | 路径 | 功能 |
| --- | --- | --- |
| POST | `/run` | 编译 LaTeX 项目 |
