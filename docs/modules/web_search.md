# 联网搜索模块（Web Search）

> 后端工具模块，提供多引擎联网搜索能力，支持 Function Calling 集成。

## 概述

联网搜索模块提供标准化的互联网搜索接口，可作为 LLM Function Calling 的工具使用。当前已集成到 Writing Chat 模块中，支持 LLM 自主判断是否需要联网搜索。DDG Challenge 验证码提交通过通用路由 `/api/search/ddg-challenge` 暴露，不绑定特定业务模块。

## 核心文件

| 文件 | 职责 |
|------|------|
| `backend/utils/web_search/` | 搜索模块包 |
| `backend/utils/web_search/__init__.py` | 模块入口，向后兼容导出所有公共接口 |
| `backend/utils/web_search/models.py` | SearchResult / SearchResponse 数据模型 |
| `backend/utils/web_search/base.py` | BaseSearchEngine 抽象基类 |
| `backend/utils/web_search/manager.py` | SearchManager + 全局单例 + Function Calling schema |
| `backend/utils/web_search/engines/ddg.py` | DuckDuckGo HTML 引擎 + DDG Challenge 处理 |
| `backend/utils/web_search/engines/searxng.py` | SearXNG 引擎 |
| `backend/utils/web_search/engines/mock.py` | Mock 引擎（测试/降级） |
| `backend/core/tool_registry.py` | Function Calling 工具注册表 |

## 架构设计

### 数据模型

- `SearchResult`: 单条搜索结果（title / snippet / url / source）
- `SearchResponse`: 一次搜索的完整响应，含 `format_for_llm()` 方法将结果格式化为 LLM 可读文本
- `DDGChallengeError`: DDG Challenge 异常，携带 challenge_id、图片 base64 数据、提交参数

### 引擎抽象

```python
class BaseSearchEngine(ABC):
    name: str
    async def search(query: str, max_results: int = 5) -> SearchResponse
    async def health_check() -> bool
```

当前内置：
- `DuckDuckGoHtmlEngine`: DuckDuckGo HTML（no-JS）端点，支持 Challenge 自动检测
- `SearXNGEngine`: SearXNG 实例 JSON API，无需验证码，适合自建实例
- `MockSearchEngine`: 空结果，用于测试和降级

### DuckDuckGo HTML 引擎

通过 POST 表单提交搜索请求，模拟浏览器行为访问 `https://html.duckduckgo.com/html/`。

**反爬绕过策略**：
- 使用可配置的 Chrome User-Agent（`web_search.user_agent`）
- 携带完整 `Sec-Fetch-*` 头（`navigate` / `document` / `same-origin` / `?1`）
- 设置 `Referer: https://html.duckduckgo.com/`
- 使用 `httpx.AsyncClient` 异步请求

**Challenge 处理流程**：
1. DDG 返回 HTTP 202（challenge 页面）
2. 引擎解析 challenge HTML，提取图片哈希和表单参数
3. 下载 challenge 图片并编码为 base64
4. 存储 challenge 元数据到内存（TTL 5 分钟）
5. 抛出 `DDGChallengeError`
6. `SearchManager` 不降级，直接向上传播
7. `writing_chat/service.py` 捕获并推送 `captcha_challenge` SSE 事件到前端
8. 用户选择正确的鸭子图片并提交
9. 后端调用 `DuckDuckGoHtmlEngine.submit_challenge()` 提交解答
10. 验证成功后引导用户重新提问

关于 DuckDuckGo HTML 端点 Challenge 验证的细节，可查看 [duckduckgo-challenge.md](../duckduckgo-challenge.md)。

### SearXNG 引擎

通过 SearXNG 实例的 JSON API 执行搜索（`GET /search?q=...&format=json`），无需验证码，适合自建 SearXNG 实例。

**使用方式**：
1. 部署 SearXNG 实例（参考 [SearXNG 安装文档](https://docs.searxng.org/admin/installation.html)）
2. 在 `config.json` 中设置 `web_search.primary_engine` 为 `"searxng"`
3. 设置 `web_search.engines.searxng.base_url` 为实例地址（如 `http://localhost:8080`）
4. 确保实例的 `settings.yml` 中启用了 JSON 格式输出（`search.formats: [json]`）

```yml
search:
  formats:
    - html
    - json
```

**响应格式**：SearXNG 返回标准 JSON，每条结果包含 `title`、`url`、`content`（摘要）和 `engine`（来源引擎名）等字段。

### SearchManager

统一搜索接口，管理引擎优先级与自动降级：

```python
manager = get_search_manager()
result = await manager.search("LaTeX table formatting")
```

**降级策略**：主引擎失败时自动降级到其他可用引擎。`DDGChallengeError` 不降级，直接向上传播以触发前端验证流程。

### ToolRegistry

Function Calling 工具注册表，统一管理所有可用工具：

```python
registry = get_tool_registry()
result = await registry.execute("web_search", {"query": "..."})
```

## 配置

```json
{
  "features": {
    "web_search": {
      "enabled": false,
      "primary_engine": "duckduckgo",
      "max_results": 5,
      "timeout_seconds": 10,
      "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36",
      "engines": {
        "duckduckgo": {
          "enabled": true
        },
        "searxng": {
          "enabled": true,
          "base_url": ""
        }
      }
    },
    "writing_chat": {
      "web_search_enabled": true
    }
  }
}
```

- `web_search.enabled`: 全局开关（默认关闭）
- `web_search.primary_engine`: 默认搜索引擎标识（`duckduckgo` / `searxng`）
- `web_search.user_agent`: 浏览器 User-Agent 字符串，用于反爬绕过（默认 Chrome UA）
- `web_search.engines.<name>.enabled`: 各引擎独立开关，未启用的引擎不会注册到 SearchManager
- `web_search.engines.searxng.base_url`: SearXNG 实例地址（如 `http://localhost:8080`），需确保实例启用了 JSON 格式输出
- `writing_chat.web_search_enabled`: Writing Chat 模块级开关
- 两级开关需同时开启才会在聊天中注入搜索工具

## 与 Writing Chat 集成

### 工具调用流程

```
用户消息 → LLM（带 tools 参数）→ 检测到 tool_call
  → 推送 SSE "searching" 事件
  → 执行 SearchManager.search()
    → DDG 触发 challenge？
      → 是：推送 SSE "captcha_challenge" 事件 → 终止搜索
      → 否：返回搜索结果
  → 推送 SSE "tool_result" 事件
  → 将结果注入 messages → 重新调用 LLM → 流式输出
```

### SSE 事件扩展

| 事件类型 | 字段 | 说明 |
|----------|------|------|
| `searching` | `query`, `engine` | LLM 决定搜索，开始执行 |
| `tool_result` | `tool`, `result_count` | 搜索完成，返回结果数量 |
| `captcha_challenge` | `challenge_id`, `images`, `query`, `engine` | DDG 触发 Challenge，推送图片到前端 |

### ChatSendRequest 新增字段

```python
class ChatSendRequest(BaseModel):
    web_search_enabled: bool | None = None  # None=跟随配置，True=强制启用，False=强制禁用
```

### DDG Challenge API

```
POST /api/search/ddg-challenge
Body: { "challenge_id": "...", "selected_hashes": ["hash1", "hash2", ...] }
Response: { "success": true/false, "message": "...", "results": [...] }
```

## 扩展指南

### 新增搜索引擎

1. 在 `backend/core/config.py` 中新增引擎配置模型，并加入 `WebSearchEnginesConfig`
2. 在 `backend/utils/web_search/engines/` 中创建新引擎文件，实现 `BaseSearchEngine` 子类
3. 在 `backend/utils/web_search/engines/__init__.py` 中导出新引擎
4. 在 `backend/utils/web_search/manager.py` 的 `get_search_manager()` 中根据 `engines.<name>.enabled` 注册

```python
def get_search_manager() -> SearchManager:
    global _search_manager
    if _search_manager is None:
        _search_manager = SearchManager()
        cfg = get_config()
        engines_cfg = cfg.features.web_search.engines

        engine_registry: dict[str, BaseSearchEngine] = {}
        if engines_cfg.duckduckgo.enabled:
            engine_registry["duckduckgo"] = DuckDuckGoHtmlEngine()
        if engines_cfg.searxng.enabled:
            engine_registry["searxng"] = SearXNGEngine()

        primary = cfg.features.web_search.primary_engine
        for name, engine in engine_registry.items():
            _search_manager.register_engine(engine, primary=(name == primary))
    return _search_manager
```

4. 在 `config.example.json` 的 `web_search.engines` 下添加新引擎默认配置
5. 在前端 `SettingsDialog.jsx` 中添加引擎卡片（开关 + 引擎特有字段）
6. 引擎切换后需调用 `reset_search_manager()` 使配置生效（`update_config_from_dict()` 已自动处理）

### 新增 Function Calling 工具

1. 在 `backend/core/tool_registry.py` 中定义工具 schema
2. 实现执行函数（async callable）
3. 注册到 `ToolRegistry`：

```python
registry = get_tool_registry()
registry.register("my_tool", schema, my_executor)
```

4. 在需要使用的 Service 中调用 `registry.get_enabled_tools()` 获取工具列表
