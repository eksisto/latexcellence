# 联网搜索模块

后端工具模块，提供多引擎联网搜索能力，支持 Function Calling 集成。当前已集成到 Writing Chat 模块中。

## 核心文件

| 文件 | 职责 |
| --- | --- |
| `backend/utils/web_search/` | 搜索模块包 |
| `models.py` | SearchResult / SearchResponse 数据模型 |
| `base.py` | BaseSearchEngine 抽象基类 |
| `manager.py` | SearchManager + 全局单例 + 查询重写 + Deep Read 编排 + Function Calling schema |
| `deep_read.py` | DeepReader — 抓取搜索结果页面正文并提取主要内容 |
| `query_rewriter.py` | QueryRewriter — LLM 搜索查询重写 + 规则兜底 |
| `engines/ddg.py` | DuckDuckGo HTML 引擎 + Challenge 处理 |
| `engines/searxng.py` | SearXNG 引擎 |
| `engines/mock.py` | Mock 引擎（测试/降级） |
| `backend/core/tool_registry.py` | Function Calling 工具注册表 |

## 架构设计

### 引擎抽象

`BaseSearchEngine`：`search(query, max_results)` / `health_check()`

内置引擎：
- **DuckDuckGo Html**：通过 POST 访问 `html.duckduckgo.com/html/`，模拟浏览器行为（Chrome UA + Sec-Fetch 头）。DDG 触发 challenge 时返回 HTTP 202，引擎解析 challenge 图片并抛出异常向上传播，前端展示验证码由用户手动提交。
- **SearXNG**：通过 JSON API 执行搜索，无需验证码，适合自建实例。
- **Mock**：空结果，用于测试和降级。

### SearchManager

统一搜索接口，管理引擎优先级与自动降级。`DDGChallengeError` 不降级，直接向上传播触发前端验证。

### ToolRegistry

Function Calling 工具注册表，统一管理所有可用工具。`web_search` 工具在 `get_search_manager()` 初始化时通过 `registry.register(name="web_search", ..., config_path="features.function_calling.web_search.enabled")` 注册，与配置中的功能开关绑定。

### Deep Read

`DeepReader` 在 `_web_search_executor` 中串联在搜索之后，对搜索结果的前 N 条并发抓取正文：

1. 使用 `httpx.AsyncClient` 抓取页面 HTML（仅 HTTPS，拒绝非 HTTPS URL）
2. trafilatura 从 HTML 中提取正文，去除导航/广告等噪音元素
3. 正文写入 `SearchResult.full_content`，通过 `format_for_llm()` 注入 LLM 上下文
4. 抓取失败或非 HTTPS 时静默跳过，不影响搜索结果

### Query Rewriting

`QueryRewriter` 在搜索前将用户自然语言查询拆解为多条精准查询，提高覆盖率：

- **LLM 优先**：调用 LLM 分析用户意图，生成 2-4 条多样化查询变体（关键词查询、自然语言表达、英文查询、子角度查询）
- **规则兜底**：LLM 失败时降级为规则方法（去疑问词 → 关键词提取 → 去重）
- 多条查询通过 `SearchManager.search_multi()` 并行执行，结果按 URL 去重合并

## 配置

`features.function_calling.web_search`（位于 `function_calling` 配置组下）：

| 字段 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `enabled` | bool | false | 全局开关 |
| `primary_engine` | str | duckduckgo | 主搜索引擎 |
| `max_results` | int | 5 | 返回结果数上限（1-20） |
| `timeout_seconds` | int | 10 | 单次搜索超时 |
| `deep_read_pages` | int | 2 | Deep Read 抓取前 N 条正文（0=禁用） |
| `deep_read_max_chars` | int | 4000 | 每页最大抓取字符数 |
| `query_rewriting.enabled` | bool | true | 是否启用 LLM 查询重写 |

两级开关（`function_calling.web_search.enabled` + `writing_chat.web_search_enabled`）需同时开启才会在聊天中注入搜索工具。

## 与 Writing Chat 集成

用户消息 → LLM（带 tools）→ 检测 tool_call → 推送 SSE "searching" → 查询重写（可选）→ 执行搜索（单条或多条并行）→ Deep Read（可选）→ 推送 "tool_result"（含 `deep_read_pages`、`rewritten_queries` 字段）→ DDG challenge 则推送 "captcha_challenge" → 重新调用 LLM → 流式输出。

SSE 事件类型：`searching`、`tool_result`、`captcha_challenge`。

DDG Challenge 提交 API：`POST /api/search/ddg-challenge`（challenge_id, selected_hashes）。
