# Markdown 转 LaTeX 模块 `markdown_converter/`

通过 `subprocess` 调用 pandoc 将 Markdown 文本转换为 LaTeX 代码，转换后自动分析所需宏包。

| 维度 | Markdown 转换 | 其他模块 |
| --- | --- | --- |
| 外部依赖 | pandoc + LLM（宏包分析） | latexmk / LLM |
| 是否修改源文件 | 否（返回结果，手动复制） | 是/否 |

## Service 核心方法

| 方法 | 功能 |
| --- | --- |
| `convert(markdown_text)` | 检查 pandoc → asyncio.to_thread 执行 pandoc → 宏包分析 → 返回 LaTeX |
| `_check_pandoc` | 检查 pandoc 是否可用 |
| `_run_pandoc` | subprocess.run 调用 `pandoc -f markdown -t latex --wrap=preserve` |

**降级策略**：无 API Key 或 LLM 分析失败时，直接返回 pandoc 转换结果。

## API 路由

Prefix: `/api/markdown-converter`, Tag: `markdown_converter`

| 方法 | 路径 | 功能 |
| --- | --- | --- |
| POST | `/convert` | Markdown 文本转 LaTeX |
