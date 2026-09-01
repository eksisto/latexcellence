# 多模型支持设计方案（模型档案 + 角色槽位路由）

> **状态：已实施**（P1–P6 全部合入）。本文保留设计共识与评审决策，作为后续变更对照
> 基线；实现形态以代码为准，实现细节见 [backend-core.md](backend-core.md)（后端）与
> [frontend.md](frontend.md)（前端）。
> 日期：2026-09-01
> 适用范围：后端 LLM 配置结构与客户端层、设置 API、前端设置分区
>
> 阶段提交对照：P1 配置结构与自动迁移 `44b5522`（含评审修复 `351c507`）→ P2 路由器
> 与客户端池 `f517726` → P3 调用点迁移 `8c98329` → P4 设置 API `bd9857e` → P5 前端
> `26c27e9` → P6 文档同步（本文更新所在提交）。

## 1. 背景与现状

LaTeXcellence 目前只支持单一 API + 单一模型：

- `LLMConfig`（`backend/core/config.py:53`）持有 `api_key / base_url / model / temperature /
  max_tokens / request_interval / max_concurrency / context_window_tokens /
  models_probe_url / extra_body` 一组全局字段；
- `LLMClient`（`backend/core/llm_client.py:140`）为全局单例，`get_llm_client()`
  （`backend/core/llm_client.py:988`）是所有模块获取客户端的统一入口；配置热重载时经
  `reset()` 重建（`backend/core/config.py:1149` 附近接线）；
- 各功能模块（OCR、逐段检查、Agent、写作聊天等）共用同一个模型，无法按任务特性选择。

不同任务对模型的需求差异很大：

| 任务特性 | 代表功能 | 适合的模型 |
| --- | --- | --- |
| 单次调用、高价值、可等待 | 模板生成、科研绘图、样式修改等 Agent | 思维链/推理模型，有视觉能力更佳（可自检生成结果） |
| 硬性依赖图像输入 | 公式 OCR、表格 OCR | 必须视觉模型，无替代 |
| 高频逐段循环调用 | 错字查找、表达优化、翻译等 | 快速便宜模型，推理模型过慢不可用 |

单一模型结构迫使用户在"能力"与"速度/成本"之间二选一，或手动改配置来回切换。

已有相关基础：

- `extra_body` 字段已为厂商私有参数预留（思维链降档、关闭思考等），说明推理模型
  兼容问题已被部分预见，但仅在全局生效；
- `writing_chat.summary_model`（`backend/core/config.py:366`）是现有唯一的"按调用
  覆写模型名"先例（`compaction.py:169` 消费），但它只能覆写模型名，无法换
  `base_url / api_key`；
- 所有模块都经由 `get_llm_client()` 取客户端，改造有统一收口，无需逐模块重写调用逻辑。

## 2. 目标与非目标

### 目标（本期）

- 配置结构升级为「模型档案（profiles）列表 + 任务槽位（assignments）指派」，每个
  档案可配置独立的 `base_url / api_key / model`；
- 按任务角色（槽位）自动路由模型，功能调用无需用户手动切换；
- 档案声明能力（`vision` / `reasoning`），保存配置时硬校验槽位与能力匹配；
- 槽位未指派时回退 `default` 槽位，功能永不中断；
- 旧单模型配置自动迁移（读取时合成为内置 default 档案），存量用户零感知；
- 推理模型兼容在客户端封装层统一处理，各 service 无感知；
- 前端新增「模型管理」设置分区（档案增删改 + 槽位指派），各功能入口展示当前使用的
  模型名。

### 非目标（本期不做）

- 多用户模式下的用户级模型配置（各用户自有 API key / 模型偏好）。本期档案与槽位为
  服务端全局配置，多用户模式下所有用户共享服务方配置；用户级配置为未来扩展方向；
- 非 OpenAI 兼容协议的原生接入（Anthropic Messages、Google generateContent 等），
  继续统一走 OpenAI 兼容接口；
- 思维链内容（`reasoning_content`）的前端展示；
- 连接测试按钮、能力自动探测（能力声明靠用户勾选）；
- 按负载自动路由、故障转移、成本统计。

## 3. 设计决策记录（评审结论）

以下决策已经过逐项评审确认：

| 决策点 | 结论 | 理由 |
| --- | --- | --- |
| 路由模式 | 角色槽位路由（非全局手动切换） | 防止"忘切换导致 OCR 用了非视觉模型"；用户零操作 |
| 多用户归属 | 仅服务端全局配置，本期不做用户级 | 改动范围小，先验证价值 |
| 档案字段 | 基础连接三件套 + capabilities 能力声明 | 能力用于前端过滤与后端硬校验 |
| 槽位粒度 | 细粒度（逐段类各自独立）+ default 兜底 | 控制力最强，未来功能自动落 default |
| 旧配置兼容 | 自动迁移（读取时合成） | 存量用户零感知 |
| 能力校验 | 硬校验（非法配置拒绝保存） | 错误在配置期暴露，而非运行期失败 |
| 槽位缺失 | 回退 default | 功能永不中断 |
| 推理模型兼容 | 封装层统一适配，service 无感知 | 不依赖 JSON 重试兜底 |
| API key 安全 | 维持明文存 config.json（现状） | 与项目定位一致，不引入密钥管理复杂度 |
| 前端范围 | 模型设置分区 + 功能处显示当前模型 | 不做连通性测试按钮（列为未来项） |

## 4. 配置结构设计

`llm` 配置节扩展为「全局默认字段 + profiles 列表 + assignments 指派表」。顶层字段
保留双重职责：作为内置 default 档案的数据源，以及档案可选项的继承基线。

```jsonc
"llm": {
  // ── 现有顶层字段全部保留，语义不变 ──
  "api_key": "", "base_url": "...", "model": "deepseek-flash",
  "temperature": 0.3, "max_tokens": 16384,
  "request_interval": 1.5, "max_concurrency": 5,
  "context_window_tokens": 0, "models_probe_url": "", "extra_body": {},

  // ── 新增：模型档案列表 ──
  "profiles": [
    {
      "id": "deepthink",            // 唯一标识，^[a-zA-Z0-9_-]+$，非空
      "name": "深度思考模型",         // 展示名
      "base_url": "https://...",
      "api_key": "...",
      "model": "glm-5.3",
      "capabilities": ["vision", "reasoning"],  // 闭集：vision / reasoning
      // ── 以下为可选覆盖项，null / 0 / {} 表示继承顶层默认值 ──
      "temperature": null,
      "max_tokens": null,
      "request_interval": null,     // 每档案独立节流（各自 Semaphore + 间隔）
      "max_concurrency": null,
      "context_window_tokens": 0,   // 窗口探测按档案独立进行
      "models_probe_url": "",
      "extra_body": {}              // 思维链降档等厂商私有参数按档案配置
    }
  ],

  // ── 新增：槽位指派表（空字符串 = 跟随 default 槽）──
  "assignments": {
    "default": "",              // "" = 使用内置 default 档案（顶层字段合成）
    "agent": "deepthink",
    "ocr": "vision-pro",
    "writing_chat": "",
    "typo_check": "",
    "expression_opt": "",
    "code_review": "",
    "terminology_check": "",
    "paragraph_translate": ""
  }
}
```

设计要点：

- **顶层字段不废弃**：保证旧配置零迁移成本，且"只配一个模型"的用户完全不用理解
  profiles 概念；
- **可选项继承**：`temperature / max_tokens / extra_body` 等在档案层为可选覆盖，
  缺省继承顶层。推理模型常见的差异化配置（不支持 temperature、需要 `extra_body`
  思维链参数、上下文窗口不同）都能按档案独立表达；
- **节流按档案隔离**：`request_interval / max_concurrency` 覆盖项使慢速 API 与快速
  API 各自持有独立的 Semaphore 与请求间隔，互不拖累；
- **槽位解析规则**（两级，无环）：`role → assignments[role]`，为空则
  `assignments["default"]`，仍为空则内置 default 档案；
- **assignments 值校验**：必须为空串或已存在的档案 id（Pydantic `model_validator`），
  OCR 槽解析到的档案必须含 `vision` 能力，违规配置在启动加载与 `/api/config` 保存时
  一律拒绝（与现有 Pydantic 校验门禁行为一致）；
- **能力闭集**：`capabilities` 取值限 `{vision, reasoning}`，未来按需扩集；
  `reasoning` 为提示性标注（前端展示徽标），仅 `vision` 参与硬校验。

### 自动迁移

不回写文件、读取时合成：`AppConfig` 校验层检测 `llm.profiles` 为空时，在内存中合成
`id="__default__"` 的内置档案（取值即顶层字段），全部槽位经两级解析自然落到它。用户
首次在设置界面保存配置时，新结构随整体写盘自然落盘。行为与现状完全一致。

## 5. 后端架构设计

### 5.1 模型路由器（新模块 `backend/core/model_router.py`）

```
get_client_for(role: str) -> LLMClient
```

- 按槽位解析规则解析到目标档案，从客户端池 `{profile_id: LLMClient}` 取实例；
- 池中客户端按档案指纹（`base_url / api_key / model`）惰性校验，指纹变化时重建内部
  `AsyncOpenAI`（双保险，主失效路径仍是配置热重载的 `reset()` 钩子）；
- 热重载接线：`backend/core/config.py` 现有的 `get_llm_client().reset()` 调用点改为
  清空整个客户端池；
- `get_llm_client()` 保留为 `get_client_for("default")` 的薄别名，降低存量测试与
  未迁移代码的破坏面。

`LLMClient` 改造为档案感知：

- 构造函数增加 `profile_id: str | None = None`；
- 内部新增 `_resolve_cfg()`：每次请求实时解析「顶层字段 + 档案覆盖项」的合并视图，
  不在 `__init__` 缓存任何配置（遵守 AGENTS.md 配置热重载规范）；
- 每个客户端实例持有独立的 `AsyncOpenAI`、Semaphore 与请求间隔状态。

### 5.2 推理模型统一兼容（封装层，service 无感知）

在 `LLMClient` 请求/响应路径统一处理：

- **请求侧参数适配**：部分推理模型拒绝 `temperature / top_p` 或要求
  `max_completion_tokens`。捕获明确的参数类 4xx 错误后，自动去除不支持参数重试一次
  并记录日志；厂商私有参数继续走档案级 `extra_body` 显式透传（现状机制）；
- **响应侧清洗**：JSON 路径解析前剥离 `content` 中的 `<think>...</think>` 片段；
  `reasoning_content` 字段天然不参与解析，无需处理；
- **流式**：`reasoning_content` 增量本期忽略，SSE 事件协议不变；
- 不依赖 `retry_with_validation()` 的 JSON 重试来兜底模型格式问题（重试仍只服务其
  原有职责）。

### 5.3 能力校验

- 校验逻辑以 Pydantic `model_validator` 实现在 `AppConfig`（或 `LLMConfig`）层，
  启动加载与 `/api/config`（`main.py`）保存共用同一道门禁；
- 规则：档案 `id` 唯一且格式合法；`assignments` 引用的档案必须存在；OCR 槽解析结果
  必须含 `vision`；错误消息给出具体槽位与档案名，面向用户展示（注意错误消息国际化
  与路径脱敏规范——本场景不涉及路径，重点是可读）。

### 5.4 解析结果查询端点

新增轻量端点 `GET /api/llm/routes`（挂载与权限跟随现有 `/api/config` 的门禁策略）：

```jsonc
// 响应（不含 api_key）
{ "routes": [ { "role": "ocr", "profile_id": "vision-pro",
                "profile_name": "视觉模型", "model": "...", "capabilities": ["vision"] }, ... ] }
```

供前端在功能入口处展示"当前模型"徽标；多用户模式下数据同样为服务端全局解析结果。

> 实施裁决（评审确认）：权限不跟随 `/api/config` 的管理员门禁，改为「多用户模式已登录
> 即可读」（`require_login_if_multiuser` 依赖）——依据 §7.2 徽标对所有登录用户展示、
> 模型为服务端全局只读配置，且响应不含 `api_key` / `base_url`，泄露面仅模型名；单用户
> 模式仍受共享访问令牌中间件约束。

## 6. 槽位与调用点映射

| 槽位 | 覆盖的调用点（现 `get_llm_client()` 消费方） | 能力约束 | 模型建议 |
| --- | --- | --- | --- |
| `default` | 兜底：编译错误翻译（`compile_error_translator.py`）、搜索查询重写/规划（`web_search/planner.py`）、`format_converter` 宏包分析兜底、`bib_manager` LLM 路径等辅助调用 | 无 | 快速通用模型 |
| `agent` | `template_gen`、`plot_gen`、`style_modify`（Agent 与 service） | `reasoning` 建议 | 推理模型，视觉能力更佳 |
| `ocr` | `formula_ocr`、`table_ocr` | `vision` 必需（硬校验） | 视觉模型 |
| `writing_chat` | `writing_chat` 会话（含编辑器助手端点 `editor_chat.py`） | 无 | 主力对话模型 |
| `typo_check` | 逐段错字检查（`base_paragraph_check` 子类） | 无 | 快速模型 |
| `expression_opt` | 逐段表达优化 | 无 | 快速模型 |
| `code_review` | LaTeX 代码审查 | 无 | — |
| `terminology_check` | 术语一致性（含 `extractor.py`） | 无 | 快速模型 |
| `paragraph_translate` | 逐段翻译 | 无 | 快速模型 |

说明：

- Agent 三模块合为一槽（评审确认）：同为单次高价值任务，模型偏好一致，避免设置页
  膨胀；未来确有分化需求再拆；
- `writing_chat.summary_model`（会话压缩模型覆写）行为保持不变——它覆写的是模型名
  而非档案，本期不改造、不单开槽位；
- 辅助调用（编译错误翻译、查询重写等）跟随 `default`，不单独设槽；这些调用低频且
  无特殊能力要求，`default` 建议配置为快速通用模型即可。

## 7. 前端设计

### 7.1 模型管理设置分区

`frontend/src/components/settings/` 下新增模型管理分区组件（由 `SettingsDialog.jsx`
组装）：

- **档案列表**：卡片式增删改，字段为名称 / base_url / api_key / model / 能力勾选
  （vision、reasoning）+ 可选覆盖项（折叠区，默认继承全局）；
- **槽位指派**：每个角色一个下拉框，选项按能力过滤（如 OCR 槽只列出含 vision 的
  档案），并提供「跟随 default」空选项；`default` 槽额外提供「使用全局默认配置」
  （即内置档案）选项；
- 同步事项（AGENTS.md 配置同步规范）：
  - 更新 `SettingsDialog.jsx` 的 `handleSave` / `setEnabledFeatures` 同步逻辑；
  - 更新后端 `AppConfig` 模型保证结构与 config.json 一致（本计划 §4）；
  - 本次不新增 WelcomePage `featureGroups` 条目——模型管理是配置分区而非功能开关，
    不属于点线目录的功能开关范畴；
  - 新增页面文字补齐 i18n 三语（zh / zh-TW / en）。

### 7.2 功能处显示当前模型

- 各 LLM 功能入口（OCR 上传区、逐段检查页、Agent 页、写作聊天侧栏等）展示当前解析
  模型徽标（如「视觉模型 · glm-4.6v」），点击跳转设置对话框对应分区；
- 数据源为 `GET /api/llm/routes`，进入页面时拉取，不随每次请求刷新；
- 多用户模式下同样展示（模型为服务端全局配置，对用户只读）。

## 8. 测试计划

后端（pytest，Mock LLM，严禁真实网络请求）：

- 配置迁移：`profiles` 为空时合成内置档案、全部槽位解析一致；`profiles` 存在时不合成；
- 校验门禁：OCR 槽指向非 vision 档案拒绝；assignments 引用不存在档案拒绝；id 重复
  拒绝；
- 路由解析：槽位缺失回退 default、两级解析、指纹变化重建客户端、热重载清空池；
- 封装层兼容：参数 4xx 降参重试、`<think>` 剥离、`extra_body` 透传（mock `AsyncOpenAI`）；
- 存量回归：现有测试通过 `get_llm_client()` 别名与新路由行为保持兼容。

前端（node --test，跟随现有惯例）：

- 槽位选项按能力过滤的纯函数；设置分区数据组装的纯逻辑。

## 9. 实施阶段

| 阶段 | 内容 | 验收标准 |
| --- | --- | --- |
| P1 配置结构与自动迁移 | `ModelProfile` / assignments 模型、校验 validator、读取时合成、`config.example.json` 更新 | 迁移与校验单测通过；旧 config.json 加载行为不变 |
| P2 路由器与客户端池 | `model_router.py`、`LLMClient` 档案化、`_resolve_cfg()`、池与 reset 接线、推理模型参数适配与响应清洗 | 路由/回退/重建单测通过；封装层兼容单测通过 |
| P3 调用点迁移 | 按 §6 映射表将各模块 `get_llm_client()` 替换为 `get_client_for(role)` | 全部调用点迁移；存量测试回归通过 |
| P4 设置 API | `/api/llm/routes` 端点、`/api/config` 保存校验生效路径、错误消息国际化条目 | API 测试通过；非法配置保存被拒绝且错误可读 |
| P5 前端 | 模型管理设置分区、`SettingsDialog` 同步、功能处模型徽标、i18n 三语 | 设置可完成档案增删改与槽位指派；徽标展示正确 |
| P6 文档同步 | DEVELOPMENT.md、`docs/backend-core.md`、PROJECT_SUMMARY.md、README.md、DESIGN.md 及相关模块文档 | 文档与实现一致，无硬编码数量描述 |

阶段依赖：P1 → P2 → P3 为严格串行；P4、P5 可在 P2 后并行；P6 收尾。

## 10. 风险与开放问题

- **不同 provider 的 OpenAI 兼容程度差异**（工具调用格式、思维链字段命名、参数白名单）
  是最大不确定点。缓解：封装层统一适配 + 档案级 `extra_body` 逃生口；首个实施版本
  建议用两个不同厂商的档案实测 Agent 与 OCR 全链路；
- **上下文窗口探测按档案隔离**：现有探测缓存需以档案为 key，避免跨档案串味（P2 落实）；
- **多用户 LLM 治理兼容性**：`llm_governance` 限流在请求准入层、不绑定具体客户端实例，
  预期天然兼容，P3 迁移时需用多用户测试回归确认；
- **全局节流语义变化**：节流从全局单例变为按档案隔离后，`default` 档案的并发行为应
  与现状一致（继承顶层字段），P2 需验证；
- **开放问题（已裁决，随实施落地）**：档案删除时其上引用的槽位如何处理 → **实施结论**：
  双保险——前端对被任一槽位引用的档案禁用删除入口，后端 `/api/config` 保存校验以
  「`assignments` 引用的档案必须存在」兜底拒绝；编辑器助手等新增调用点是否需要独立
  槽位 → **实施结论**：编辑器助手端点（`editor_chat`）跟随 `writing_chat` 槽，不单开
  槽位。
