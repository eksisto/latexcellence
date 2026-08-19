# Writing Chat 网络访问灰度与运维

本文说明网络访问 v2 的灰度、观测、容量检查、故障演练、缓存维护和回滚流程。架构与质量目标见 [Writing Chat 网络访问架构](writing-chat-web-access-architecture.md)，模块行为见 [Writing Chat](modules/writing_chat.md)。

## 灰度开关

所有开关通过设置页修改并支持配置热重载。首次灰度按下表逐项推进，不要同时开启所有 v2 路径。

| 能力 | 兼容/回滚值 | v2 值 |
| --- | --- | --- |
| 通用搜索 | `web_search.strategy=legacy` | `aggregate` |
| 网页读取 | `web_search.page_reading.strategy=legacy` | `structured` |
| 浏览器回退 | `browser.enabled=false` | `true` |
| 学术排序 | `scholarly_research.ranking_mode=legacy` | `rrf` |
| 学术全文 | `scholarly_research.full_text_enabled=false` | `true` |
| Writing Chat 证据包 | `writing_chat.evidence_bundle_enabled=false` | `true` |

推荐顺序：先分别切换聚合搜索、结构化读取、学术排序和学术全文，最后在具备 Playwright 浏览器二进制的部署中小流量启用浏览器回退。

## 观测

发布观察应至少汇总：成功率、p50/p95 延迟、v2 失败率、缓存命中率、浏览器升级率、低质量抽取率和未验证引用率。URL、query hash 和 session ID 不应作为 metrics label；需要排障时只在受控日志中使用匿名 hash 关联事件。

## 离线评测与容量检查

先校验冻结数据集并重放已经采集的预测：

```shell
python tools/web_access_eval/run_eval.py validate
python tools/web_access_eval/run_eval.py replay --dataset <dataset.json> --predictions <predictions.json>
```

本地结构化抽取容量冒烟测试会报告成功数、总耗时、p50/p95 和 Python 峰值内存。阈值必须来自部署环境预算；下面的值仅示例如何让超预算时返回非零退出码：

```shell
python tools/web_access_eval/stage6.py capacity --iterations 100 --concurrency 8 --max-p95-ms 500 --max-peak-memory-mb 256
```

该命令使用仓库自有 HTML fixture，不包含网络、Provider、浏览器或 LLM 延迟，不能替代预发布环境的端到端容量测试。真实灰度必须另行记录搜索、抓取、浏览器池、学术 Provider、LLM 工具轮次和整条 SSE 请求的 p95 与资源占用。

## 故障与安全演练

以下离线测试覆盖引擎超时/熔断、HTTP 重试与阻断、SSRF/重定向、缓存隐私和 Writing Chat 证据链：

```shell
python -m pytest tests/test_utils/test_web_search_aggregation.py tests/test_utils/test_web_fetch_resilience.py tests/test_utils/test_web_page_reading.py tests/test_api/test_writing_chat.py -q
```

浏览器启用前，还应在隔离的预发布环境验证浏览器二进制缺失、进程崩溃、池耗尽、跨协议导航、下载、弹窗、登录、付费墙和 CAPTCHA。真实搜索引擎或学术 API 不作为 pytest 的通过条件。

## 缓存维护

网页原始响应和抽取缓存位于 `data/web_cache/`，受 TTL、响应大小和单条目大小限制。修改 `extractor_version` 会使旧抽取缓存自然失效。需要立即清理时运行：

```shell
.\clean-cache.ps1 -NoPause
```

Bash 环境运行 `./clean-cache.sh`。清理会删除网页缓存以及项目的 Python、pytest 和前端构建缓存，不删除 Writing Chat 会话中已持久化的证据片段。

## 回滚演练

先校验示例配置仍保留全部回滚开关：

```shell
python tools/web_access_eval/stage6.py rollback-check
```

演练时依次把搜索和网页读取切回 `legacy`，关闭浏览器、学术全文与 EvidenceBundle，并把学术排序切回 `legacy`。发送一个需要联网与一个需要学术检索的问题，确认兼容工具契约、SSE、旧会话恢复和 URL 引用校验仍工作。回滚期间保留缓存但停止结构化读取即可；不要在稳定期结束前删除 legacy 代码或迁移开关。

## 发布记录

每次发布保存以下证据：冻结评测报告、容量报告、故障/安全测试结果、回滚演练时间与结果、依赖/许可证核对结果。只有连续评测没有严重回归、安全测试通过、部署环境 p95 和资源占用处于预算内且回滚演练成功，才能满足阶段 6 退出条件。
