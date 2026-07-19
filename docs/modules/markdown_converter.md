# Markdown 转 LaTeX 模块 `markdown_converter/`

## 设计特点

Markdown 转 LaTeX 模块**不使用 LLM**——通过 `subprocess` 调用 `pandoc` 命令行工具，
将 Markdown 文本转换为 LaTeX 代码。与编译模块类似，属于外部工具调用而非 LLM 文本修改。

| 维度 | Markdown 转换 | 其他模块 |
|------|--------------|---------|
| 外部依赖 | pandoc（需用户自行安装） | latexmk / LLM API |
| 输入 | 纯 Markdown 文本 | LaTeX 源码 / 图片 |
| 输出 | LaTeX 代码 | 修改后的文本 / 编译结果 |
| 是否修改源文件 | 否（返回结果，手动复制） | 是 / 否 |

## `service.py` — 转换业务逻辑

### 核心方法

| 方法 | 功能 |
|----|----|
| `convert(markdown_text)` | 异步：检查 pandoc → 调用 asyncio.to_thread 执行 pandoc → 返回 LaTeX |
| `_check_pandoc()` | 静态方法：检查 pandoc 是否可用（`shutil.which`） |
| `_run_pandoc(markdown_text)` | 同步：subprocess.run 调用 `pandoc -f markdown -t latex --wrap=preserve` |

### 转换流程

```
1. 校验 markdown_text 非空
2. 检查 pandoc 是否已安装（未安装返回明确的错误信息）
3. 通过 asyncio.to_thread 在独立线程中调用 pandoc
4. pandoc 读取 stdin 中的 Markdown，输出 LaTeX 到 stdout
5. 检查返回码，失败时抛出 RuntimeError
```

### pandoc 命令行

```
pandoc -f markdown -t latex --wrap=preserve
```

`--wrap=preserve` 保留原始的换行方式，避免 pandoc 自动折行。

## `router.py` — API 路由

Prefix: `/api/markdown-converter`, Tag: `markdown_converter`

| 方法 | 路径 | 功能 |
|----|---|----|
| `POST` | `/convert` | 将 Markdown 文本转换为 LaTeX（接受 `markdown` 字段） |

### 请求体

```json
{ "markdown": "# 标题\n\n内容..." }
```

### 返回

```json
{ "latex": "\\section{标题}\n\n内容\\ldots" }
```
