# 联网搜索模块

后端工具模块，提供多引擎联网搜索，支持 Function Calling 集成。当前已集成到 Writing Chat 模块中。

## 核心文件

| 文件 | 职责 |
| --- | --- |
| `backend/utils/web_search/` | 搜索模块包 |
| `models.py` | SearchResult / SearchResponse / PageReadResult / FindInPageResult 数据模型 |
| `base.py` | BaseSearchEngine 抽象基类 |
| `manager.py` | SearchManager + 全局单例 + 查询重写 + Deep Read 编排 + Function Calling schema |
| `deep_read.py` | DeepReader — 抓取搜索结果页面正文并提取主要内容 |
| `query_rewriter.py` | QueryRewriter — LLM 搜索查询重写 + 规则兜底 |
| `engines/ddg.py` | DuckDuckGo HTML 引擎 + Challenge 处理 |
| `engines/searxng.py` | SearXNG 引擎 |
| `engines/tavily.py` | Tavily Search API 引擎 |
| `engines/mock.py` | Mock 引擎（测试/降级） |
| `backend/core/tool_registry.py` | Function Calling 工具注册表 |

## 架构设计

### 引擎抽象

`BaseSearchEngine`：`search(query, max_results)` / `health_check()`

内置引擎：

- **DuckDuckGo Html**：通过 POST 访问 `html.duckduckgo.com/html/`，模拟浏览器行为（Chrome UA + Sec-Fetch 头）。DDG 触发 challenge 时返回 HTTP 202，引擎解析 challenge 图片并抛出异常向上传播。前端展示验证码，由用户手动提交。
- **SearXNG**：通过 JSON API 执行搜索，无需验证码，适合自建实例。
- **Tavily**：通过 Tavily Search API（`POST https://api.tavily.com/search`）执行搜索，无需处理验证码。配置 API Key 时使用 Bearer Token 鉴权。未配置 Key 时自动携带 `X-Tavily-Access-Mode: keyless` 请求头走 Tavily 免费公共接口（免费、限速，响应结构与带 Key 完全一致）。默认使用 `search_depth="advanced"`，并开启 `include_answer`（合成答案）与 `include_raw_content`（原始正文直接填充 `full_content`，避免 Deep Read 二次抓取）。
- **Mock**：空结果，用于测试和降级。

### SearchManager

统一搜索接口，管理引擎优先级与自动降级。`DDGChallengeError` 不降级，直接向上传播触发前端验证。配置的主引擎未注册（如 Tavily 未启用）时自动降级到第一个可用引擎并记录警告。

`search_multi()` 合并多条查询结果时按 URL 去重，合并上限为 `max(max_results, min(max_results × 查询数, 15))`，避免把多角度查询的收益截断回单条查询数量，同时防止搜索结果淹没上下文。

### ToolRegistry

Function Calling 工具注册表，统一管理所有可用工具。`get_search_manager()` 初始化时注册三个工具（均绑定 `features.function_calling.web_search.enabled` 开关）：

- `web_search`：搜索互联网，返回标题/摘要/URL（默认 10 条）
- `open_page`：抓取指定 URL 正文（标题 + 全文，复用全局共享的 `DeepReader`，`max_chars` 限制 500–20000）
- `find_in_page`：在指定页面内定位关键词，返回匹配前后上下文（内部按最多 20 万字符搜索，`context_chars` 50–2000、`max_matches` 1–20）

其中 `open_page` / `find_in_page` 模拟 Codex 的 `search → open_page → find_in_page` agentic 检索模式，由模型自行决定深入阅读哪些页面。

### Deep Read

`DeepReader` 提供页面抓取与页内查找能力：

1. 使用进程级共享的 `httpx.AsyncClient` 抓取页面 HTML（仅公网 HTTPS：拒绝非 HTTPS，并拦截私网/环回/链路本地/保留地址，防止 SSRF。重定向最多跟随 5 跳且每跳重新校验。超时按请求传入，客户端在服务关闭时统一释放）
2. trafilatura 从 HTML 中提取标题与正文，去除导航/广告等噪音元素
3. 正文写入 `SearchResult.full_content` 或 `PageReadResult.content`，通过 `format_for_llm()` 注入 LLM 上下文
4. 结果带 5 分钟 TTL 内存缓存，全局共享的 `DeepReader` 实例供 `open_page`、`find_in_page` 与自动 Deep Read 复用，避免重复请求
5. `find_in_page()` 使用朴素字符串查找（小写化 + `find` 循环）定位关键词，返回匹配上下文，避免正则灾难性回溯

`_web_search_executor` 仍支持按配置自动 Deep Read 前 N 条（`deep_read_pages > 0`），默认关闭（`0`），改为由模型通过 `open_page` / `find_in_page` 按需读取。

`format_for_llm()` 统一以 `UNTRUSTED DATA` 包装头开头（防 prompt injection），并按字符预算截断页面全文，避免多轮搜索撑爆上下文。

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
| `primary_engine` | str | tavily | 主搜索引擎（未注册时自动降级） |
| `max_results` | int | 10 | 返回结果数上限（1-20） |
| `timeout_seconds` | int | 10 | 单次搜索超时 |
| `engines.tavily.enabled` | bool | true | 是否启用 Tavily 引擎 |
| `engines.tavily.api_key` | str | "" | Tavily API Key（设置页掩码展示，保存时保留原值） |
| `deep_read_pages` | int | 0 | 自动 Deep Read 前 N 条（0=禁用，由 open_page 按需读取） |
| `deep_read_max_chars` | int | 10000 | 每页最大抓取字符数 |
| `query_rewriting.enabled` | bool | true | 是否启用 LLM 查询重写 |

两级开关（`function_calling.web_search.enabled` + `writing_chat.web_search_enabled`）需同时开启才会在聊天中注入搜索工具。

Tavily 引擎在 `engines.tavily.enabled = true` 时注册到 `SearchManager`。已配置 API Key 时使用 Bearer 鉴权，未配置时自动以 keyless 模式调用（免费、限速）。引擎禁用时自动跳过，由其他可用引擎降级兜底。

## 与 Writing Chat 集成

用户消息 → LLM（带 tools）→ 检测 tool_call → 推送 SSE `tool_status` → 查询重写（可选）→ 执行搜索（单条或多条并行，多查询合并上限 15 条）→ 自动 Deep Read（可选，默认关闭）→ 推送 `tool_result`（含 `search_results`、`deep_read_pages`、`rewritten_queries` 字段）→ 模型按需调用 `open_page` / `find_in_page` → 重复调用 LLM 直至无工具调用 → 流式输出。

SSE 事件类型：`tool_status`（搜索执行中）、`tool_result`（含 `search_results`）、`captcha_challenge`。

DDG Challenge 提交 API：`POST /api/search/ddg-challenge`（challenge_id, selected_hashes）。
