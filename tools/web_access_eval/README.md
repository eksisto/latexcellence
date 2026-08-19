# Writing Chat 网络访问离线评测

本目录保存网络访问阶段 0 的可版本化评测集、仓库自有页面 fixture 和确定性重放工具。评测工具不发起搜索、网页抓取、学术 API 或 LLM 请求；输入是系统已经保存的 JSON 预测结果。

数据集覆盖 `search`、`scholarly`、`extraction` 和 `grounding`。搜索与学术数据只保存公开 URL、稳定标识符和人工标签；网页正文 fixture 由本项目编写，避免再分发第三方页面内容。

## 用法

从项目根目录运行：

```shell
python tools/web_access_eval/run_eval.py validate
python tools/web_access_eval/run_eval.py collect-structured-extraction --output tools/web_access_eval/results/structured-extraction.json
python tools/web_access_eval/run_eval.py replay --dataset tools/web_access_eval/datasets/extraction.json --predictions tools/web_access_eval/results/structured-extraction.json --output tools/web_access_eval/results/structured-extraction-report.json
python tools/web_access_eval/run_eval.py replay `
  --dataset tools/web_access_eval/datasets/search.json `
  --predictions results/legacy/search.json `
  --output results/legacy/search-report.json
```

`replay` 输出逐 case 指标和聚合指标。搜索包含 nDCG、MRR、Recall、权威来源命中、重复率、域名多样性、延迟分位数和引擎失败数量；学术、抽取和 grounding 指标见 `metrics.py`。缺少预测的 case 会降低覆盖率，但不会伪造延迟值。

预测文件必须使用 `models.py` 中对应 suite 的 Pydantic 契约。新增数据集时保持 `schema_version`，并在提交前运行 `validate`。

## 运行时采集与技术 spike

采集命令只在维护者明确允许时访问真实服务；pytest 和 `replay` 始终离线：

```shell
python tools/web_access_eval/legacy.py --suite search `
  --dataset tools/web_access_eval/datasets/search.json `
  --output tools/web_access_eval/results/legacy-search.json --allow-network
python tools/web_access_eval/legacy.py --suite search `
  --dataset tools/web_access_eval/datasets/search.json `
  --output tools/web_access_eval/results/aggregate-search.json `
  --search-strategy aggregate --allow-network
python tools/web_access_eval/legacy.py --suite extraction `
  --dataset tools/web_access_eval/datasets/extraction.json `
  --output tools/web_access_eval/results/legacy-extraction.json
python tools/web_access_eval/spike.py --output tools/web_access_eval/results/stage0-spike.json
```

`search` 和 `scholarly` 采集器记录可见的异常。`--search-strategy aggregate` 会记录 v2 `EngineStatus` 中的超时、限流、阻断、熔断和其他失败；legacy 管理器隐藏的引擎/Provider 失败会写入 `measurement_gaps`，不会伪造失败数量。`grounding` 采集器只接受脱敏的 Writing Chat trace JSON，因此不会在评测命令中调用 LLM。

## 阶段 6 检查

`stage6.py` 提供本地结构化抽取容量冒烟和默认回滚配置检查：

```shell
python tools/web_access_eval/stage6.py capacity --iterations 100 --concurrency 8
python tools/web_access_eval/stage6.py rollback-check
```

容量命令不访问网络，不能替代预发布环境的搜索、浏览器、Provider 与端到端 SSE 压测。灰度指标、故障演练和发布记录要求见 [`docs/web-access-operations.md`](../../docs/web-access-operations.md)。
