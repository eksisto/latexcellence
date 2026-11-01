# 评测回归基线 CI（eval-regression）

> 把 `tools/` 下的人工评测工具接入 GitLab CI：提示词、模型、解析器、语料或评测工具本身变更后自动跑评测，与入库基线比对指标，回归即失败，防止质量无声回退。

---

## 1. 组成

| 文件 | 职责 |
| --- | --- |
| `.gitlab-ci.yml` | CI 入口，job 分「离线门禁」与「LLM 评测」两档（另有与评测无关的 check 门禁阶段，见下方分工说明） |
| `tools/ci/collect_metrics.py` | 指标采集：把各套评测的原始产物（results.json / 重放报告）归一化为统一的 metrics.json |
| `tools/ci/compare_metrics.py` | 基线比对与更新：回归时非零退出；`--update-baseline` 播种/刷新基线 |
| `tools/ci/metric_rules.py` | 共享规则：指标方向登记表（higher/lower）、suite 分类与默认容差 |
| `tools/ci/build_config.py` | 从 `config.example.json` 生成 CI 评测用 `config.json`（拒绝覆盖已有文件，保护本地真实配置） |
| `tools/ci/llm_probe.py` | LLM 连通性探测（最小 chat 调用，`max_retries=1` 快速失败） |
| `tools/ci/baselines/<suite>.json` | 入库基线（指标 + 容差覆盖 + 更新记录） |

各套人工评测工具自身的用法见 `tools/<suite>_eval/README.md`。本文只讲 CI 接入层。

> **与代码门禁的分工**：本文件只讲评测回归。仓库 CI 另有一条与评测无关的**代码门禁**（`.gitlab-ci.yml` 的 `check` 阶段，任意 MR 与默认分支 push **全量**触发，不做路径过滤）。这些 job 只负责装依赖，检查命令全部来自 `scripts/check_backend.sh` / `scripts/check_frontend.sh`，与本地统一验证入口 `scripts/check.sh` 同源。两条链路的分工与载体见 [docs/development.md 的「约束机器化」](development.md)。

## 2. Job 分档

| Job | 内容 | 外部依赖 | 触发 |
| --- | --- | --- | --- |
| `offline-gate` | 术语/翻译语料校验、Prompt 理解用例 `--dry-run`、web_access 数据集校验、各网络访问评测套件**离线重放**（search / scholarly / extraction legacy / extraction structured）+ 基线比对 | 无（零成本） | MR / 默认分支 push 路径命中即跑 |
| `llm-evals` | 错字、表达优化、术语一致性、逐段翻译四套语料评测 + Prompt 理解评测（`--concurrency 4`）+ 基线比对 | CI/CD 变量 `LX_LLM_KEY`；LLM 调用量随语料规模线性增长 | MR / 默认分支 push（有 Key 才跑，无 Key 警告跳过）、定时、手动 |

**触发路径**（MR 与默认分支 push 相同）：`backend/core/prompts.py`、`llm_client.py`、`base_paragraph_check.py`、`latex_parser.py`、`json_utils.py`、`latex_json_guard.py`、各评测对应的功能模块与 fixtures、`tools/*_eval/**`、`tools/eval_common.py`、`tools/ci/**`、`config.example.json`、`.gitlab-ci.yml` 自身。

**定时**：每周一 02:00 UTC 全量回归。用途：服务方可能在仓库无任何变更的情况下升级 `llm.base_url` 指向的托管模型，只有定期重跑才能捕捉这种"模型漂移"（GitLab 不支持在文件内声明，需在 UI「构建 → 流水线计划」配置，见 5.1）。

**手动**（GitLab「运行流水线」变量，见 5.1）：`run_llm`（`RUN_LLM`）/ `update_baseline`（`UPDATE_BASELINE`）开关。

## 3. 指标与判定

### 3.1 各 suite 指标（方向：↑越大越好 / ↓越小越好）

| Suite | 指标 |
| --- | --- |
| typo_check / expression_opt | `detection_rate`↑、`missed_items`↓、`missed_expected`↓、`false_positive_items`↓、`false_positive_changes`↓、`errors`↓ |
| terminology_check | 上表条目级指标 + `cluster_hit_rate`↑、`clusters_partial`↓、`clusters_missed`↓ |
| paragraph_translate | `translation_rate`↑、`phrase_matched`↑、`phrase_missed`↓、`unmatched_changes`↓、`false_positive_items`↓、`errors`↓ |
| formula_ocr / table_ocr | `normalized_match_rate`↑（归一化匹配率，近似指标）、`empty_results`↓、`low_confidence`↓、`errors`↓（formula 另有 `inline_mismatch`↓） |
| prompt_comprehension | `pass_rate`↑、`pass`↑、`partial`/`fail`/`errors`↓ + **逐用例状态回归**（PASS→PARTIAL→FAIL→ERROR 排名上升即回归，规则与该工具自带 `compare.py` 一致） |
| web_search / web_scholarly / web_extraction / web_extraction_structured | 重放报告 aggregate：nDCG@10、MRR@10、Recall@20、char/block precision-recall 等；延迟类指标只记录不判定 |

OCR 的 `normalized_match_rate` 是把 `strip_ocr_latex` 清洗 + 空白折叠后的期望 .tex 与识别结果做**精确相等**比较的比率。等价改写（不同宏包、不同写法）不算匹配，所以该指标偏保守，只作回归观察信号。最终结论以对应 `report.html` 人工确认为准。

### 3.2 判定规则（`compare_metrics.py`）

- **lower 指标**：`current > baseline + tol` 算回归。**higher 指标**对称（`current < baseline - tol`）
- `tol` 默认按 suite 类别取（见 3.3），基线文件的 `"tolerances"` 字段可按指标覆盖
- 基线有而本次**缺失**的指标 → 失败（防止采集口径变化静默掩盖回归）。本次**新增**指标只提示，不判定（提示需要把新指标登记进 `metric_rules.DIRECTIONS`）
- 比对报告写入 `logs/ci/regression_report.json` 并随 artifact 上传
- 无基线的 suite 默认放行并打印播种指引。`--require-baseline` 可收紧为失败（全部 suite 播种完成后建议在 workflow 中开启）

### 3.3 默认容差

| Suite 类别 | rate 容差 | count 容差 | 适用 |
| --- | --- | --- | --- |
| deterministic | 0 | 0 | web 离线重放（给定预测文件结果完全确定，超 0 即真实变更） |
| llm | 0.05 | 2 | 四套语料评测 + Prompt 理解（temperature 下漏检/误报有 1~2 条的天然波动） |
| ocr | 0.1 | 2 | 公式/表格 OCR |

LLM 生成有随机性，比较必须是容差式的。容差过小会造成 CI 频繁误报、使人失去信任。若某 suite 波动明显偏大，在基线文件里按指标调大 `"tolerances"`，并在 PR 里说明理由。

## 4. 基线治理

基线是入库的 JSON（`tools/ci/baselines/<suite>.json`），记录指标快照 + 更新时间 + commit。**基线变更必须走 PR 人工审核**——它等价于"宣告当前质量水平可接受"。

**播种 / 更新**（本地或 CI）：

```bash
# 1. 跑评测（LLM 套件需 config.json 配置 Key）
python tools/typo_check_eval/run_eval.py --output-dir logs/ci/typo_check
# 2. 采集指标
python tools/ci/collect_metrics.py --suite typo_check \
    --results logs/ci/typo_check/results.json --output logs/ci/metrics/typo_check.json
# 3a. 比对（回归时 exit 1）
python tools/ci/compare_metrics.py --metrics-dir logs/ci/metrics --baselines-dir tools/ci/baselines
# 3b. 或把本次结果写为基线（保留已有 tolerances）
python tools/ci/compare_metrics.py --metrics-dir logs/ci/metrics --baselines-dir tools/ci/baselines --update-baseline typo_check
```

CI 内也可手动触发并把 `UPDATE_BASELINE` 置为 `true`：各 job 会以 `--update-baseline` 运行并上传 `tools/ci/baselines/` 为 artifact，下载后人工审核 diff 再提交。`--update-baseline` 模式恒返回 0，不做回归判定。

**什么时候该更新基线**：

- 语料增删（分母变化，所有比率指标含义改变）——**必须**随语料 PR 一起更新
- 指标口径变更（`collect_metrics.py` / `metric_rules.py` 改动）
- 有意接受的质量变化：提示词调优带来"误报下降但漏检微增"这类权衡，PR 里说明后更新

**什么时候不该更新**：比对已经报回归、且你解释不了回归原因时。先看该 suite 的 `report.html` / `results.json` artifact 定位具体条目，确认是波动（重跑一次验证）还是真回退。

## 5. CI 平台配置（一次性）

CI 载体为 GitLab CI（`.gitlab-ci.yml`），需在自托管 GitLab 实例 / 仓库上一次性完成下列配置：

### 5.1 Runner、变量与流水线计划

两个 eval job（offline-gate / llm-evals）运行环境为 `python:3.12-slim` 镜像，均安装 `tools/ci/requirements-minimal.txt` 轻量依赖集（评测工具真实导入的最小集合，不含 playwright / numpy 等重依赖——冷缓存首跑在慢速 PyPI 链路上全量安装曾撑爆 offline-gate 超时）。pip 缓存按清单哈希跨流水线复用。check 阶段两个 job 另需能拉取 `node:20-bookworm-slim`。

- **Runner**：自托管 [GitLab Runner](https://docs.gitlab.com/runner/)（Docker executor），需能拉取上述镜像并访问 `llm.base_url` 指向的 LLM API。
- **CI/CD 变量**（`设置 → CI/CD → 变量`）：
  - `LX_LLM_KEY`：LLM 评测 job 需要，建议勾选 Masked。**未配置时该 job 不会创建**（离线门禁照常运行）。
  - `EVAL_LLM_BASE_URL` / `EVAL_LLM_MODEL`（可选）：覆盖评测用 `llm.base_url` / `llm.model`，映射到 `build_config.py` 读取的环境变量。
  - `PIP_INDEX_URL`（可选）：pip 镜像源。CI 已默认使用清华 TUNA 镜像（`check_backend.sh --install` 与 eval job 的 before_script 均以 `${PIP_INDEX_URL:-默认值}` 形式 export），如需换其他镜像在此配置即可覆盖。
- **定时全量**：UI `构建 → 流水线计划` 新建，cron `0 2 * * 1`（每周一 02:00，捕捉托管模型漂移），此时全部 job 运行。
- **手动触发**：「运行流水线」时覆盖流水线变量：`RUN_LLM`（默认 `true`）、`UPDATE_BASELINE`（置 `true` 时以 `--update-baseline` 运行并上传基线 artifact）。
- **路径过滤语义**：MR 流水线基于与目标分支的 diff 判定（主力路径，最可靠）。默认分支 push 基于推送前 SHA，连续多次 push 或批量合并可能漏判，以 MR 门禁为准。feature 分支 push 不创建流水线。
- **防并发**：`resource_group`（按 ref 串行）+ `interruptible`（新流水线可取消运行中的旧 MR 流水线）。

**bootstrap 步骤**：配置 `LX_LLM_KEY` → 手动「运行流水线」（`RUN_LLM=true`）→ 检查 artifact 中各 LLM suite 的结果是否合理 → 再次手动触发（`UPDATE_BASELINE=true`）→ 审核并提交 `tools/ci/baselines/` 新增文件 → 此后 MR 自动进入回归门禁。web 四套基线已随本方案播种（离线重放确定性可复现，容差为 0）。

## 6. 设计要点

- **静默回退防护**：语料式评测在 LLM 不可用时会把失败静默回退为原文（产出一堆假 clean 结果且 exit 0）。防护有两层：`llm_probe.py` 在跑评测前先做最小调用，失败即终止 job。结果里 `errors` 指标本身参与回归判定。
- **web_access 重放为何能进离线门禁**：预测文件已入库（`tools/web_access_eval/results/legacy-*.json`），replay 只做确定性打分，可守护指标计算与数据集本身的回归。structured 抽取套件走本地 HTML fixture 经 `ExtractorRegistry` 采集后重放，额外守护抽取器代码。grounding 套件暂无入库预测，待补齐后再播种基线。
- **OCR 评测不进 CI**：公式/表格 OCR 评测需要 Vision 模型与 TeX Live 安装（拉慢 job），历史上以独立 job 仅在定时/手动触发，现已从 CI 移除。`tools/formula_ocr_eval/`、`tools/table_ocr_eval/` 及 `metric_rules.py` 的 ocr suite 规则保留，需要时可在本地手动跑评测并按第 4 节流程播种/比对基线。
- **输出目录**：CI 统一写 `logs/ci/`（已被根 `.gitignore` 覆盖），`results.json` + `report.html` 随 artifact 上传供人工查看。

## 7. 已知限制

- 容差式比对是统计防线，不能替代抽样人工复核。重要提示词改动建议仍按各工具 README 做一轮人工评估。
- `normalized_match_rate` 只反映"逐字一致"的比例，OCR 语义等价的改写会被计为不匹配，观察趋势而非绝对值。
- 语料式评测的宽松命中判定（子串 + 显著词元）继承自 `tools/eval_common.py` 的 `analyze_hits`，同样只作辅助信号。
