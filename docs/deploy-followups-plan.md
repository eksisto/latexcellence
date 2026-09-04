# 多用户部署侧跟踪项收口设计方案

> **状态：未实施。** 本文收口散落在多份已定稿
> 文档中的部署侧跟踪项：rar 解压器纳入沙箱（[deployment.md](deployment.md)
> 安全风险声明跟踪项）、真实压缩 RAR 样本验收（[rar-extractor-fallback.md](rar-extractor-fallback.md)
> 遗留验收项）、沙箱容器 warm pool（[multi-user-v2-plan.md](multi-user-v2-plan.md)
> 非目标/风险表"若实测劣化明显再列为优化项"）、模型连通性测试
> （[multi-model-plan.md](multi-model-plan.md) 非目标"列为未来项"）。
> 作为分阶段实施的依据与变更对照基线。如行为与本文冲突，先修订本文再动代码。

## 1. 背景与动机

上述四项分别指向四个缺口：

1. **安全口径不一致**：latexmk / pandoc / latexindent / git / 绘图 Python
   均已经 SandboxRunner 收口（无网络、只读根 fs、资源限额），唯 rar 解压器
   仍以后端子进程直跑，以宿主进程身份解析攻击者完全可控的压缩包字节流
   （`deployment.md` 安全风险声明"已知例外"段）；
2. **验收有欠账**：rar 管线的测试 fixtures 全部是纯字节手工构造的 store
   模式 RAR4（`tools/rar_fixtures/make_fixtures.py`），压缩流无法伪造，
   "真实压缩 RAR4 与 RAR5 端到端上传成功"至今未验证；
3. **沙箱冷启动成本**：每编译一次一个容器，冷启开销约数百毫秒到秒级
   （v2 风险表原判"可接受；若实测劣化明显再列为优化项"——多用户并发与
   低配部署（树莓派/闲置设备）场景下该项会放大）；
4. **多模型运维盲区**：模型档案与槽位路由已实施，但配置保存后无法在
   界面验证"这个档案到底通不通"，排障要靠改代码或 curl。

## 2. 范围与非目标

### 范围

- rar 解压执行层迁移至 SandboxRunner（convert 镜像内置 unar + bsdtar，
  暂存只读挂载，安全基线对齐其他外部命令）；
- 真实压缩 RAR4/RAR5 fixtures 入库与端到端用例（双解压工具参数化）；
- Docker 模式沙箱容器预创建池（warm pool）与冷启动延迟观测；
- `POST /api/llm/test` 连通性测试端点 + 模型管理分区"测试连接"入口。

### 非目标（本轮明确排除）

- 容器常驻复用（一个容器跨多次执行服务多请求）：tmpfs `/tmp` 跨执行残留
  构成跨用户状态泄漏面，与"每次执行一个全新容器"的卫生模型冲突；预创建
  池若实测仍不达标再另行立项评估（须配套逐租约清理方案）；
- 多机横向扩展下的全局池协调（池为副本本地资源，随非目标沿袭 v2）；
- 注册邮箱验证、管理员强制清理（沿袭 v2 非目标，不在本文范围）；
- 槽位级连通性测试（首轮仅档案级，见 Q8）；
- rootless Docker / Podman（沿袭 deployment.md 后续演进方向，不承诺）。

## 3. 关键决策记录

| # | 决策点 | 结论 |
| --- | --- | --- |
| Q1 | 解压器落位镜像 | 按 deployment.md 跟踪项既定口径：convert 镜像 apt 增装 `unar` 与 `libarchive-tools`（bsdtar），构建期 `command -v` 自检清单同步补充；backend 镜像维持现状（local 回落路径仍用宿主 unar） |
| Q2 | ExecSpec 形态 | `ExecSpec(command=解压命令, cwd=extract_dir（默认 rw 挂载）, image_kind="convert", mounts=[{host_path: 压缩包绝对路径, read_only: True}], timeout_seconds=既有 deadline)`；ExecSpec 无 stdin 契约与现状 `stdin=DEVNULL` 语义天然一致；命令沿用 `RAR_EXTRACTOR_CMD_BUILDERS` 注册表（unar 禁交互参数、bsdtar 禁 `-P` 的既有形状不变） |
| Q3 | 工具选择语义 | `resolve_rar_extractor()` 的 `shutil.which` 探测仅对 local 后端保留（宿主语义）；docker 后端不探测容器内 PATH，按 unar 优先固定顺序取注册表 builder（镜像保证两者齐备）；测试用强制工具覆写钩子（现 `force_rar_tool` fixture）保留 |
| Q4 | mid-flight 配额拦截 | 已确认方案 A：`SandboxRunner.execute` 契约扩展可选 `cancel_event: asyncio.Event`（docker 后端杀容器、local 后端沿用 `terminate_process_tree` 击杀进程组），现有 `_start_rar_watch` 监视线程改写为宿主侧 asyncio 任务（extract_dir 经 bind mount 对宿主可见，dir_size 采样逻辑不变），超配额/超时置位 cancel_event；方案 B（放弃 mid-flight 拦截）不予采用，拦截强度与现网持平 |
| Q5 | 主机侧管线保留 | `reject_protected_rar`（RAR4 头链预扫）、`normalize_rar_names`（bsdtar 文件名归一化）、`_validate_extracted`、大小写冲突扫描、零产物兜底全部留在宿主进程（纯 Python，无外部命令），在容器执行前后照常运行 |
| Q6 | 沙箱不可用语义 | mode=docker 硬失败按既有 `sandbox_unavailable` → 上传链路 503；mode=auto 探测失败自动回落 local（行为与现网一致，零回归）；单用户模式不涉及 rar 上传，零影响 |
| Q7 | warm pool 形态 | 预创建池（默认开启已确认）：docker 后端按 image_kind 维护"已创建未启动"容器池，池深 = `max_concurrent_global`；租约获取后 pop → start → exec → stop → remove → 异步补充（补充任务并发 ≤ 2）；执行语义与现网完全一致（每执行一个全新容器，无跨执行状态）；local 模式与池无关 |
| Q8 | 连通性测试粒度 | 仅档案级（`profile_id` 直测该档案本身）；槽位生效结果已有 `/api/llm/routes` 只读展示，不做槽位级测试按钮 |
| Q9 | 掩码协议边界 | 后端测试直接读服务端配置真实值（`get_config()` / `LLMClient(profile_id=...)`），前端只传 `profile_id`，**严禁**前端回传 api_key（前端持有的是 `***xxxx` 掩码；掩码串按 `update_config_from_dict` 既有协议绝不落盘/外发） |
| Q10 | 新增配置 | `sandbox.warm_pool_enabled: bool = True`（仅 docker 模式生效）——同步 `config.example.json`、设置页 SandboxSection、`docs/development.md` 配置段；连通性测试不新增配置项 |
| Q11 | 阶段顺序 | 一 rar 沙箱化 → 二 真实样本（紧随其后，复用一的真容器冒烟基建）→ 三 warm pool → 四 连通性测试（一/二 与 三/四 两组可并行推进） |

## 4. 总体设计

### 4.1 阶段一：rar 解压器纳入 SandboxRunner

- **镜像**：`docker/convert/Dockerfile` apt 层增装 `unar` 与
  `libarchive-tools`，构建期自检 `command -v unar bsdtar pandoc`；经
  `build.sh` 重建并更新 `docker/README.md` 镜像内容表；
- **执行层重写**（`backend/core/multiuser/upload_rar.py`）：
  - `extract_rar()` 的 Popen 管线段（`subprocess.Popen` +
    `communicate(timeout)`）替换为 `ExecSpec` 构造 +
    `get_sandbox_runner().execute(spec, user_id=current_sandbox_user_id())`；
  - `_kill_process_group` 保留（local 后端路径复用）；`_start_rar_watch`
    线程改写为宿主侧 asyncio 监视任务，配额 breach 走 Q4 方案 A 的
    `cancel_event`（契约扩展落在 `runner.py` execute 签名与两个后端的
    取消实现上，`ExecSpec`/`ExecResult` 模型不变）；
  - `resolve_rar_extractor()` 收敛为 local 后端专用（Q3）；
  - 超时语义：现有 deadline 折算为 `spec.timeout_seconds` +
    `ExecResult.timed_out`，错误码与用户文案不变。
- **复用不变**：解压暂存目录（`get_staging_root()/<sid>/extracted`）、
  解压后安全复核与配额记账、错误码（`rar_extractor_missing` 仅 local、
  `archive_unsafe`、`invalid_archive` 等）全部保持。
- **测试**：
  - `tests/test_multiuser/test_upload_rar.py` 既有 hermetic 用例迁移到
    execute 路径（Mock runner，校验 ExecSpec 形状：cwd、只读挂载、超时）；
  - 新增 `tests/test_docker/test_rar_sandbox_smoke.py`（marker `docker`，
    沿用 `test_sandbox_smoke.py` 的 daemon/镜像/bind-mount 三重 skip 门）：
    真容器正常包解压全链路、加密/分卷包拒绝路径、压缩包路径只读性断言
    （容器内写挂载源失败）。

### 4.2 阶段二：真实压缩 RAR4/RAR5 端到端样本

- **样本生成**：按 `make_fixtures.py` docstring 已起草的手工步骤，在
  维护者机器以 rarlab `rar` CLI 生成小型压缩样本入库
  `tests/fixtures/rar/`：`compressed_rar4.rar`（`-m5` 压缩数百字节
  `.tex`）、`compressed_rar5.rar`（`-ma5`），可选增补真实加密包与
  RAR5 分卷对（各一个，总体积控制在 KB 级）；样本为本项目自产数据，
  不分发 rarlab 软件本体，无许可负担；
- **入库校验**：`tools/rar_fixtures/` 增补 `verify_fixtures.py`（校验入库
  样本的结构特征：压缩标志位、版本头、加密/分卷标志命中预期），防坏样本
  静默入库；再生成步骤在 `make_fixtures.py` docstring 维护；
- **用例扩展**：`RAR_TOOL_PARAMS`（unar/bsdtar 双工具参数化，各自
  skipif 独立）新增真实样本用例：双工具下压缩 RAR4/RAR5 端到端上传解压
  成功；真实加密/分卷样本走拒绝路径。对应 rar-extractor-fallback.md
  §六验收口径："真实压缩 RAR4 与 RAR5 各一在 bsdtar 下端到端上传成功"
  （unar 同口径覆盖）。

### 4.3 阶段三：沙箱容器预创建池（warm pool）

- **池机制**（`backend/utils/sandbox/docker_backend.py`）：
  - 池结构：`image_kind → 已创建未启动容器` 队列，池深 =
    `max_concurrent_global`（每副本本地）；
  - 获取路径：租约（`sandbox_leases` 既有语义不变）→ 池 pop → start →
    exec → stop + remove → 异步补充；池空时按现路径即时 create（无退化）；
  - 容器参数复用 `_build_container_kwargs`（只读根 fs、无网络、cap_drop、
    tmpfs、资源限额逐字段一致），池容器统一打 label
    `latexcellence.warm_pool=1` 携带副本标识；
  - `initialize_sandbox_runner()`（lifespan 既有挂载点）预热首批；
- **对账**：warm pool 的孤儿清理为**每副本职责**，不进维护循环的选主
  分支（容器是 daemon 全局资源，副本各自按 label + 自身副本标识清理
  属于自己的池容器）——这是对维护循环"持租约者执行"语义的有意偏离，
  实施时在 multi-user-v2-plan §12.1 外置清单补记；
- **配置**：`sandbox.warm_pool_enabled`（Q10）；auto 模式探测命中 docker
  才启用；多副本部署下各副本独立建池，空闲容器总量上浮为已知代价
  （文档标注，不引入跨副本协调）；
- **观测与验收**：execute 路径增加池命中/未命中与准备耗时日志行；
  `tools/` 增补基准脚本（同一 ExecSpec 冷启 N 次对比池路径 N 次，输出
  P50/P95），验收标准：池路径准备耗时光标级下降（目标 P50 显著低于
  冷启基线）且既有 docker 冒烟（编译/转换/无网/只读根）全部保持绿色。

### 4.4 阶段四：模型连通性测试

- **后端**：`POST /api/llm/test`（`main.py`，紧邻既有 `GET /api/llm/routes`；
  多用户 `require_admin_if_multiuser`——触发真实计费调用属管理面操作，
  单用户模式登录即可）：
  - 请求 `{profile_id}` 与 `{role}` 二选一：`role` 经
    `get_client_for(role)` 解析（验证"实际生效档案"，含 default 回落），
    `profile_id` 直接构造 `LLMClient`（验证"该档案本身"）；
  - 执行：`LLMClient.chat()` 最小补全（`max_tokens=1`、"ping"），超时
    上限 15s，计测往返延迟；api_key 缺失返回结构化错误
    `api_key_missing`，网络/上游错误脱敏透传（严禁回显密钥），服务端
    日志只记 profile 标识与错误类别；
  - 响应 `{ok, latency_ms, model, profile_id, role?, error?}`；错误文案
    前端经 `translateError()` 走既有翻译链路，新增错误码补三语
    `errors` 命名空间；
  - 多用户 LLM 治理（每用户限流/租约）对本端点豁免（管理面诊断操作，
    记录为有意偏离）；
- **前端**：`ModelManagementSection.jsx` 每张档案卡片增"测试连接"按钮
  （内联展示延迟/失败原因，测试中禁用保存），三语 i18n；不新增配置项，
  无四处配置同步负担；
- **测试**：`tests/test_api/` 补端点用例——Mock `LLMClient.chat`
  （严禁真实网络）、成功/超时/缺 key 三态、多用户非管理员 403、掩码
  协议回归（`***` 前缀档案在测试路径中不落盘不变更）。

## 5. 阶段划分与验收

| 阶段 | 内容 | 验收要点 |
| --- | --- | --- |
| 一：rar 沙箱化 | convert 镜像增装解压器 + execute 迁移 + cancel_event 契约扩展 + 真容器冒烟 | docker 模式下解压在无网/只读根 fs 容器内执行（挂载断言）；auto/local 回落零回归；加密/分卷/超配额/超时行为与错误码不变；hermetic 用例迁移后全绿；deployment.md"已知例外"段与跟踪项移除 |
| 二：真实 RAR 样本 | 压缩 RAR4/RAR5（含可选加密/分卷）入库 + verify 脚本 + 双工具用例 | 真实压缩 RAR4 与 RAR5 在 unar、bsdtar 下端到端上传解压成功；真实加密/分卷样本走确定性拒绝；verify_fixtures.py 通过 |
| 三：warm pool | 预创建池 + 每副本对账 + 基准脚本 + 配置开关 | 准备耗时 P50 显著优于冷启基线；租约/并发上限语义不变（既有测试全绿）；开关关闭后行为与现网一致；池孤儿可被对账清理 |
| 四：连通性测试 | test 端点 + 档案卡片按钮 | role/profile 两种粒度返回正确生效档案；缺 key/超时/网络错误三态文案三语完整；非管理员 403；掩码协议零回归；Mock 单测覆盖 |

阶段一先行（二的真容器用例依赖其冒烟基建）；三、四与一/二无依赖可并行。

## 6. 全局约束（每阶段适用）

- 外部命令一律经 SandboxRunner 执行；ExecSpec 禁 shell 字符串、无 stdin
  契约不变；用户内容（压缩包、解压产物）永不进镜像层。
- 面向用户的错误消息只用相对路径；完整路径只进服务端日志；新增错误码
  同步三语 `errors` 命名空间。
- 新配置项走 `SandboxConfig`（`extra="forbid"`）并四点同步：
  `config.example.json`、`SandboxSection.jsx`、`docs/development.md`、
  `backend/core/config.py`。
- 每阶段同步更新对应模块文档与 `PROJECT_SUMMARY.md`；全部完成后将本文
  状态改为"已实施"，并完成勾销义务：deployment.md 安全风险声明"已知
  例外"段、rar-extractor-fallback.md §六/§七跟踪行、multi-user-v2-plan.md
  §2 非目标与 §16 风险表 warm pool 行、multi-model-plan.md §2 非目标
  "连通性测试按钮"项；`THIRD_PARTY_NOTICES.md` 第五节系统级依赖补
  convert 镜像 unar/bsdtar 条目。
