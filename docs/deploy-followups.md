# 多用户部署侧收口设计（rar 沙箱化 / warm pool / 模型连通性测试）

> **状态：已实施。** 本文记录四项部署侧收口的设计与决策依据，供维护与查询。
> 代码注释中的「deploy-followups §4.x」均指向本文编号。如行为与本文冲突，
> 先修订本文再动代码。

## 1. 背景与动机

四项收口分别指向四个缺口：

1. **rar 解压器沙箱化**：latexmk / pandoc / latexindent / git / 绘图 Python 均已经
   SandboxRunner 收口，唯 rar 解压器曾以宿主子进程直跑，解析攻击者完全可控的压缩包
   字节流——现解压命令已在 convert 镜像内执行（管线细节见
   [backend-core.md](backend-core.md) `upload_rar.py` 行与
   [rar-extractor-fallback.md](rar-extractor-fallback.md) §三）；
2. **真实压缩 RAR 样本**：测试 fixtures 曾全部是纯字节构造的 store 模式 RAR4，压缩流
   无法伪造——现真实压缩/加密/分卷样本已入库，见 rar-extractor-fallback.md §六/§八；
3. **沙箱冷启动**：每编译一次一个容器，冷启数百毫秒到秒级——warm pool（§4.3）；
4. **多模型运维盲区**：模型档案保存后无法在界面验证连通性——连通性测试端点（§4.4）。

## 2. 范围与非目标

**范围**：rar 解压执行层迁入 SandboxRunner；真实压缩 RAR4/RAR5 fixtures 入库与端到端
用例；Docker 模式沙箱容器预创建池与冷启动观测；`POST /api/llm/test` 连通性测试端点 +
模型管理分区"测试连接"入口。

**非目标**：容器常驻复用（tmpfs `/tmp` 跨执行残留构成跨用户状态泄漏面，与"每次执行
一个全新容器"的卫生模型冲突）；多机横向扩展下的全局池协调；注册邮箱验证；槽位级连通性
测试（`/api/llm/routes` 已展示生效结果）；rootless Docker / Podman。

## 3. 关键决策记录

| # | 决策点 | 结论 |
| --- | --- | --- |
| Q1 | 解压器落位镜像 | convert 镜像 apt 增装 `unar` 与 `libarchive-tools`（backend 镜像维持现状，供 local 回落） |
| Q2 | ExecSpec 形态 | cwd=解压目录（rw 挂载）、压缩包只读挂载、超时折算 `timeout_seconds`；命令沿用 `RAR_EXTRACTOR_CMD_BUILDERS` 注册表 |
| Q3 | 工具选择语义 | `shutil.which` 探测仅 local 后端保留；docker 后端不探测容器内 PATH，按 unar 优先固定顺序 |
| Q4 | mid-flight 配额拦截 | `SandboxRunner.execute` 契约扩展可选 `cancel_event`（docker 杀容器、local 击杀进程组），宿主侧 asyncio 监视任务超配额/超时置位 |
| Q5 | 宿主侧管线保留 | RAR4 头链预扫、文件名归一化、安全复核、大小写冲突扫描、零产物兜底全部留在宿主进程（纯 Python） |
| Q6 | warm pool 形态 | 按 image_kind 维护"已创建未启动"容器池，池深 = `max_concurrent_global`；租约 → 池 pop → start → exec → stop+remove → 异步补充；执行语义不变（每执行一个全新容器） |
| Q7 | 连通性测试粒度 | 仅档案级；槽位生效结果已有 `/api/llm/routes` 只读展示 |
| Q8 | 掩码协议边界 | 后端直读服务端配置真实值，前端只传 `profile_id`，**严禁**前端回传 api_key |
| Q9 | 新增配置 | `sandbox.warm_pool_enabled: bool = True`（仅 docker 模式生效）；连通性测试不新增配置项 |

## 4. 各项设计

### 4.1 rar 解压器纳入 SandboxRunner

- convert 镜像内置 unar + bsdtar（构建期 `command -v` 自检）；解压命令以 ExecSpec 描述、
  经 `get_sandbox_runner().execute()` 在容器内执行：压缩包路径只读挂载、解压目录 rw 挂载、
  既有 deadline 折算超时；
- 配额监视从线程改为宿主侧 asyncio 任务：采样 extract_dir 写盘量（bind mount 对宿主
  可见），超配额/超时置位 `cancel_event` 硬中断（docker 杀容器、local 击杀进程组）；
- 错误码与用户文案不变（`rar_extractor_missing` 仅 local、`archive_unsafe`、
  `invalid_archive` 等）；mode=docker 硬失败按 `sandbox_unavailable` → 上传 503，
  mode=auto 探测失败自动回落 local 零回归。

### 4.2 真实压缩 RAR4/RAR5 样本

以 rarlab rar CLI 生成压缩/加密/分卷样本入库（KB 级，自产数据不分发软件本体），
`tools/rar_fixtures/verify_fixtures.py` 自动对账结构特征与预扫行为；测试双工具参数化
端到端覆盖。样本清单、生成要点与实测结论见 rar-extractor-fallback.md §六/§八。

### 4.3 沙箱容器预创建池（warm pool）

- docker 后端按 image_kind 维护"已创建未启动"容器池（每副本本地），池深 =
  `max_concurrent_global`；租约获取后 pop → start → exec → stop+remove → 异步补充
  （补充并发 ≤ 2）；池空或"形状"（创建时固化的 kwargs）不一致时即时 create，无退化；
- 容器参数复用 `_build_container_kwargs`（安全基线逐字段一致）；池容器打 label
  `latexcellence.warm_pool=1` + 副本标识；**孤儿清理是每副本职责**，有意不进维护循环
  选主分支（容器是 daemon 全局资源、池是副本本地状态）；
- 配置 `sandbox.warm_pool_enabled`（默认开，auto 探测命中 docker 才启用）；
- 实现细节与运维说明见 [backend-core.md](backend-core.md) sandbox 节与
  [deployment.md](deployment.md)「warm pool 运维说明」；冷启动基准脚本
  `tools/warm_pool_bench.py`。

### 4.4 模型连通性测试

- **后端** `POST /api/llm/test`（`main.py`，多用户 `require_admin_if_multiuser`——
  触发真实计费调用属管理面操作，单用户模式登录即可）：
  - 请求 `{profile_id}` 与 `{role}` 二选一：`role` 经 `get_client_for(role)` 验证
    "实际生效档案"（含 default 回落），`profile_id` 直测该档案本身；
  - 执行最小补全（`max_tokens=1`、"ping"），超时上限 15s，计测往返延迟；api_key 缺失
    返回结构化错误 `api_key_missing`，网络/上游错误脱敏透传（严禁回显密钥）；
  - 响应 `{ok, latency_ms, model, profile_id, role?, error?}`；多用户 LLM 治理对本端点
    豁免（管理面诊断操作，有意偏离）；
- **前端**：`ModelManagementSection.jsx` 每张档案卡片"测试连接"按钮（内联展示延迟/
  失败原因，测试中禁用保存），三语 i18n；错误码经 `translateError()` 翻译链路。
