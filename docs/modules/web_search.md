# 联网搜索模块

后端工具模块，提供多引擎联网搜索能力，支持 Function Calling 集成。当前已集成到 Writing Chat 模块中。

## 核心文件

| 文件 | 职责 |
| --- | --- |
| `backend/utils/web_search/` | 搜索模块包 |
| `models.py` | SearchResult / SearchResponse 数据模型 |
| `base.py` | BaseSearchEngine 抽象基类 |
| `manager.py` | SearchManager + 全局单例 + Function Calling schema |
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

Function Calling 工具注册表，统一管理所有可用工具。

## 配置

`features.web_search`：全局开关（Pydantic 默认 `false`）、primary_engine（duckduckgo/searxng）、max_results、timeout、user_agent、各引擎独立开关及 SearXNG base_url。两级开关（`web_search.enabled` + `writing_chat.web_search_enabled`）需同时开启才会在聊天中注入搜索工具。

## 与 Writing Chat 集成

用户消息 → LLM（带 tools）→ 检测 tool_call → 推送 SSE "searching" → 执行搜索 → DDG challenge 则推送 "captcha_challenge" → 否则推送 "tool_result" → 重新调用 LLM → 流式输出。

SSE 事件类型：`searching`、`tool_result`、`captcha_challenge`。

DDG Challenge 提交 API：`POST /api/search/ddg-challenge`（challenge_id, selected_hashes）。
