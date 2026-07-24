# DuckDuckGo HTML 端点 Challenge 验证流程

## 1. 触发条件

| 触发因素 | 详情 |
| --- | --- |
| **User-Agent** | 已知 bot UA（如 `python-requests`、`curl`、`Go-http-client`、`HeadlessChrome` 等）立即触发 |
| **频率** | 同 IP 短时间内大量请求触发 |
| **缺少 Sec-Fetch 头** | `Sec-Fetch-Mode` / `Sec-Fetch-Dest` / `Sec-Fetch-Site` / `Sec-Fetch-User` 缺失或值不正确 |
| **缺少 Referer** | 不携带 `Referer: https://html.duckduckgo.com/` |
| **IP 黑名单** | IP 被标记后，后续所有请求均触发 |

正常浏览器 UA + 完整的 Fetch Metadata 头 → **HTTP 200 + 搜索结果**（无 challenge）。

## 2. Challenge 响应（HTTP 202）

当触发时，DDG 返回 **HTTP 202**，body 是一个包含两个表单的 HTML 页面：

### 表单 1: `img-form`（图片加载追踪）

```
action: //duckduckgo.com/anomaly.js?sv=html&cc=sre&ti={timestamp}&gk={gk}&p={hashes}&q={query}&o={encoded_state}&r={region}
method: POST
```

- 由浏览器 `<iframe>` 自动加载，用于追踪图片加载事件
- `ti` = 图片加载时间戳

### 表单 2: `challenge-form`（验证码提交）

```
action: //duckduckgo.com/anomaly.js?sv=html&cc=sre&st={timestamp}&gk={gk}&p={hashes}&q={query}&o={encoded_state}&r={region}
method: POST
```

## 3. 验证码类型

**"Select all squares containing a duck"** — 3x3 图片网格，用户需勾选所有包含鸭子的图片。

## 4. 参数详解

| 参数 | 说明 | 示例 |
| --- | --- | --- |
| `sv` | 服务类型 | `html` |
| `cc` | Challenge code / 区域标识 | `sre` |
| `st` | Submit timestamp（提交时间戳，秒级） | `1784686309` |
| `ti` | Image load timestamp（图片加载时间戳） | `1784686309` |
| `gk` | Challenge key（32 位 hex，全局固定 `d4cd0dabcf4caa22ad92fab40844c786`） | `d4cd0dabcf4caa22ad92fab40844c786` |
| `p` | 图片 ID 列表（`-` 分隔的 32 位 hex） | `f5c02fde6f...-fef2bb6912...-...` |
| `q` | 原始搜索查询 | `test` |
| `o` | 加密状态令牌（含原始查询的编码表示） | `yZeLIzItizkeDz8f...` |
| `r` | 区域代码 | `usw` |

## 5. 图片机制

- 图片 URL: `https://duckduckgo.com/assets/anomaly/images/challenge/{hash}.jpg`
- 图片可**无认证直接访问**（HTTP 200, image/jpeg）
- 图片是**临时性的**，challenge 过期后返回 **HTTP 410 Gone**
- 每个图片 ID 是 32 位 MD5 hex

## 6. 提交流程

**提交 body** (application/x-www-form-urlencoded):

```
challenge-submit={gk}&image-check_{hash1}={hash1}&image-check_{hash2}={hash2}&...
```

- 勾选的图片：`image-check_{hash}={hash}`（值 = checkbox name）
- 未勾选的图片：不包含在 body 中

## 7. 响应结果

| 场景 | HTTP | 页面内容 |
| --- | --- | --- |
| **正确** | 200 | 搜索结果页（含 vqd） |
| **错误（还有重试）** | 200 | "Please try again" + "You have N attempt(s) left" + Try again 按钮 |
| **耗尽重试** | 200 | 纯文本错误页 + `error-lite@duckduckgo.com` + code |
| **IP 封禁** | 200 | 同上，不再显示图片 challenge |

## 8. 追踪像素

每次请求末尾有一个追踪 `<img>` 标签：

```
https://duckduckgo.com/t/tqadb?{id}&s=lite&cc={cc}&p={hashes}&r={region}&gk={gk}&c={type}&sc={submit_count}&e={error_count}&i={attempt_index}
```

- `c`: `dadb` (初始) / `mta` (重试)
- `e`: 错误次数
- `i`: 尝试索引（`-1` = 未尝试）
- `sc`: 提交计数

## 9. 关键设计特征

1. **无 cookies**：整个 challenge 流程不使用 cookies（DDG 是无状态的）
2. **IP 级别封锁**：封锁基于 IP，非 session
3. **`o` 参数是核心**：加密状态令牌，编码了查询信息和服务器状态，每次请求都不同
4. **`gk` 是固定值**：`d4cd0dabcf4caa22ad92fab40844c786`（至少在当前版本中全局共享）
5. **`vqd` 只在结果页**：正常响应中包含 `vqd`（Validation Query Digest），用于后续分页请求
6. **解封方式**：用真实浏览器从被封 IP 发起一次搜索即可解封

## 10. 绕过策略总结

绕过 challenge 的关键是：

1. 使用真实的浏览器 User-Agent
2. 设置完整的 `Sec-Fetch-*` 头（`navigate` / `document` / `same-origin` / `?1`）
3. 设置 `Referer: https://html.duckduckgo.com/`
4. 控制请求频率
5. 在获取的 HTML 中提取 `vqd` 用于分页请求
