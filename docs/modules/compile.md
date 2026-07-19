# 自动编译模块 `compile/`

## 设计差异（与 LLM 模块的关键区别）

自动编译模块**不使用 Marker 保护策略**——不调用 LLM 修改文本，而是通过 `subprocess`
调用 `latexmk -xelatex` 编译主文件，仅在编译失败时使用 LLM 翻译 stderr 错误信息。

| 维度 | 编译模块 | 其他 LLM 模块 |
|------|---------|--------------|
| LLM 角色 | 仅用于翻译错误信息（非文本修改） | 用于修改/优化 LaTeX 文本 |
| 输入 | latexmk stderr 输出 | LaTeX 源码或纯文本 |
| 是否修改源文件 | 否（编译生成 PDF） | 是（修改 .tex 文件） |
| 耗时因素 | 外部进程（latexmk） | LLM 网络请求 |

## `service.py` — 编译业务逻辑

### Pydantic 输出模型

```python
class CompileResult(BaseModel):
    success: bool              # 编译是否成功
    stdout: str = ""           # 编译器标准输出
    stderr: str = ""           # 编译器标准错误
    explanation: str = ""      # LLM 翻译后的中文解释（仅失败时填充）
    fixes: list[str] = []      # LLM 建议的修复方法列表

class ErrorTranslation(BaseModel):
    explanation: str           # LLM 输出的中文错误解释
    fixes: list[str] = []      # LLM 输出的修复建议
```

### 核心方法

| 方法 | 功能 |
|---|---|
| `compile_latex(main_file, workspace_id)` | 异步：调用 latexmk → 捕获 stderr → 失败时 LLM 翻译错误 → 返回 CompileResult |
| `_extract_key_errors(stderr_text)` | 从 stderr 中提取关键错误行（正则匹配 `! `, `l.\d+`, `LaTeX Error:` 等），避免全文过长 |

### 编译流程

```
1. 获取项目根目录 + 验证主文件存在
2. asyncio.create_subprocess_exec("latexmk", "-xelatex", "-interaction=nonstopmode", main_file, cwd=root)
3. asyncio.wait_for(process.communicate(), timeout=latexmk_timeout)
4. 检查 returncode == 0 → 成功
5. 失败时：提取关键错误 → 调用 retry_with_validation() 翻译为中文
6. 返回 CompileResult
```

### System Prompt 核心约束

```
You are a LaTeX compilation error translator.
1. Identify the root cause of each error.
2. Explain the error in plain Chinese.
3. Provide actionable fix suggestions.
```

### 错误提取正则

匹配 latexmk/xelatex 的典型错误格式：`! `, `Runaway argument`, `Emergency stop`,
`l.\d+`, `No file `, `LaTeX Error:`, `Package .* Error:`, `Missing .* inserted`。

## `router.py` — API 路由

Prefix: `/api/compile`, Tag: `compile`

| 方法   | 路径  | 功能                                         |
|------|-----|--------------------------------------------|
| `POST` | `/run` | 编译 LaTeX 项目（接受 `main_file` 相对路径） |
