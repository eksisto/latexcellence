# 多模型支持设计（模型档案 + 角色槽位路由）

> **状态：已实施。** 本文记录模型路由机制的设计与决策依据，供维护与查询；
> 实现细节见 [backend-core.md](backend-core.md)（`model_router.py` 与 `llm_client.py`
> 档案化）与 [frontend.md](frontend.md)（模型管理分区），决策概览另见
> [DESIGN.md §17](../DESIGN.md)。代码注释中的「multi-model §N」均指向本文编号。
> 如行为与本文冲突，先修订本文再动代码。

## 1. 背景与目标

不同任务对模型的需求差异很大：Agent 类任务（模板生成、绘图、样式修改）适合推理模型；公式/表格 OCR 硬性依赖视觉模型；逐段检查/翻译等高频循环调用适合快速便宜模型。单一全局模型结构迫使用户在"能力"与"速度/成本"之间二选一。

本期目标：配置结构升级为「模型档案（profiles）列表 + 任务槽位（assignments）指派」，按任务角色自动路由；档案声明能力（`vision` / `reasoning`）并硬校验；槽位未指派回退 `default`；旧单模型配置自动迁移，存量用户零感知；推理模型兼容在客户端封装层统一处理，各 service 无感知；前端「模型管理」分区编辑，功能入口展示当前生效模型。

**非目标**：多用户模式下的用户级模型配置（档案为服务端全局配置）；非 OpenAI 兼容协议的原生接入；思维链内容前端展示；能力自动探测；按负载自动路由、故障转移、成本统计。

## 2. 核心设计决策

| 决策点 | 结论 | 理由 |
| --- | --- | --- |
| 路由模式 | 角色槽位路由（非全局手动切换） | 防止"忘切换导致 OCR 用了非视觉模型"；用户零操作 |
| 档案字段 | 连接三件套 + capabilities 能力声明 | 能力用于前端过滤与后端硬校验 |
| 槽位粒度 | 细粒度（逐段检查类各自独立）+ default 兜底 | 控制力最强，未来功能自动落 default |
| 旧配置兼容 | 自动迁移（读取时内存合成，不回写文件） | 存量用户零感知 |
| 能力校验 | 硬校验（非法配置拒绝保存） | 错误在配置期暴露，而非运行期失败 |
| 推理模型兼容 | 封装层统一适配，service 无感知 | 不依赖 JSON 重试兜底 |
| 节流语义 | 每档案独立 Semaphore 与请求间隔 | 慢速 API 与快速 API 互不拖累 |
| API key 安全 | 维持明文存 config.json（现状） | 与项目定位一致，不引入密钥管理复杂度 |

## 3. 运行时要点

- **槽位解析规则**（两级，无环）：`role → assignments[role]`，为空则 `assignments["default"]`，仍为空则内置 default 档案（顶层字段合成的 `__default__`）；
- **热重载**：配置保存时 `reset_client_pool()` 清空整个客户端池，是配置失效的主路径；客户端池另按档案指纹（base_url/api_key/model）惰性校验双保险；
- **上下文窗口探测**：按档案隔离缓存，防跨档案串味；
- **多用户 LLM 治理兼容**：治理在请求准入层、不绑定具体客户端实例，天然兼容。

## 4. 配置结构设计

`llm` 配置节 = 全局默认字段（顶层）+ profiles 列表 + assignments 指派表。顶层字段保留双重职责：内置 default 档案的数据源，以及档案可选项的继承基线。

```jsonc
"llm": {
  // ── 顶层字段（语义不变），同时作为内置 default 档案 ──
  "api_key": "", "base_url": "...", "model": "...",
  "temperature": 0.3, "max_tokens": 16384,
  "request_interval": 1.5, "max_concurrency": 5,
  "context_window_tokens": 0, "models_probe_url": "", "extra_body": {},

  // ── 模型档案列表 ──
  "profiles": [
    {
      "id": "deepthink",            // 唯一标识，^[a-zA-Z0-9_-]+$
      "name": "深度思考模型",
      "base_url": "https://...",
      "api_key": "...",
      "model": "...",
      "capabilities": ["vision", "reasoning"],  // 闭集：vision / reasoning
      // 可选覆盖项，null / 0 / {} 表示继承顶层默认值
      "temperature": null,
      "max_tokens": null,
      "request_interval": null,     // 每档案独立节流（各自 Semaphore + 间隔）
      "max_concurrency": null,
      "context_window_tokens": 0,
      "models_probe_url": "",
      "extra_body": {}              // 思维链降档等厂商私有参数按档案配置
    }
  ],

  // ── 槽位指派表（空字符串 = 跟随 default 槽）──
  "assignments": {
    "default": "",
    "agent": "deepthink",
    "ocr": "vision-pro",
    "writing_chat": "",
    "typo_check": "", "expression_opt": "", "code_review": "",
    "terminology_check": "", "paragraph_translate": ""
  }
}
```

设计要点：

- **顶层字段不废弃**："只配一个模型"的用户完全不用理解 profiles 概念；
- **可选项继承**：推理模型常见的差异化配置（不支持 temperature、需要 `extra_body` 思维链参数、不同上下文窗口）按档案独立表达；
- **assignments 值校验**：必须为空串或已存在的档案 id；OCR 槽解析到的档案必须含 `vision`；违规配置在启动加载与 `/api/config` 保存时一律拒绝（Pydantic `model_validator` 同一门禁）；
- **能力闭集**：`capabilities` 限 `{vision, reasoning}`；`vision` 参与硬校验，`reasoning` 为提示性标注（前端徽标）；
- **自动迁移**：`llm.profiles` 为空时在内存合成内置档案，用户首次保存配置时随整体写盘自然落盘。

## 5. 后端架构设计

### 5.1 模型路由器（`backend/core/model_router.py`）

`get_client_for(role) -> LLMClient`：按两级解析取目标档案，从客户端池 `{profile_id: LLMClient}` 取实例；指纹变化时重建内部 `AsyncOpenAI`。`get_llm_client()` 保留为 `get_client_for("default")` 薄别名，降低存量测试与未迁移代码的破坏面。

`LLMClient` 档案化：构造函数接收 `profile_id`；内部 `_resolve_cfg()` 每次请求实时解析「顶层字段 + 档案覆盖项」合并视图，不在 `__init__` 缓存配置（遵守配置热重载规范）；每实例独立持有 `AsyncOpenAI`、Semaphore 与请求间隔状态。

### 5.2 推理模型统一兼容（封装层，service 无感知）

- **请求侧**：部分推理模型拒绝 `temperature / top_p` 或要求 `max_completion_tokens`——捕获明确的参数类 4xx 后自动去除不支持参数重试一次并记日志；厂商私有参数走档案级 `extra_body` 显式透传；
- **响应侧**：JSON 路径解析前剥离 `content` 中的 `<think>...</think>` 片段；`reasoning_content` 天然不参与解析；
- **流式**：`reasoning_content` 增量忽略，SSE 事件协议不变；
- 不依赖 `retry_with_validation()` 的 JSON 重试兜底模型格式问题。

### 5.3 能力校验

校验逻辑以 Pydantic `model_validator` 实现在配置模型层，启动加载与 `/api/config` 保存共用同一道门禁；错误消息给出具体槽位与档案名，面向用户展示。

### 5.4 解析结果查询端点

`GET /api/llm/routes`：返回各槽位两级解析结果（`role / profile_id / profile_name / model / capabilities`，不含 api_key），供前端功能入口展示"当前模型"徽标。权限裁决：多用户模式**已登录即可读**（`require_login_if_multiuser`）——模型为服务端全局只读配置，响应不含密钥，泄露面仅模型名；单用户模式受共享访问令牌中间件约束。

另有 `POST /api/llm/test` 连通性测试端点（档案卡片"测试连接"），设计与权限见 [deploy-followups.md](deploy-followups.md) §4.4。

## 6. 槽位与调用点映射

| 槽位 | 覆盖的调用点 | 能力约束 | 模型建议 |
| --- | --- | --- | --- |
| `default` | 兜底：编译错误翻译、搜索查询重写/规划、format_converter 宏包分析兜底、bib_manager LLM 路径等辅助调用 | 无 | 快速通用模型 |
| `agent` | `template_gen`、`plot_gen`、`style_modify`（Agent 与 service） | `reasoning` 建议 | 推理模型，视觉能力更佳 |
| `ocr` | `formula_ocr`、`table_ocr` | `vision` 必需（硬校验） | 视觉模型 |
| `writing_chat` | `writing_chat` 会话（含编辑器助手端点 `editor_chat.py`） | 无 | 主力对话模型 |
| `typo_check` | 逐段错字检查 | 无 | 快速模型 |
| `expression_opt` | 逐段表达优化 | 无 | 快速模型 |
| `code_review` | LaTeX 代码审查 | 无 | — |
| `terminology_check` | 术语一致性（含 `extractor.py`） | 无 | 快速模型 |
| `paragraph_translate` | 逐段翻译 | 无 | 快速模型 |

说明：Agent 三模块合为一槽——同为单次高价值任务，模型偏好一致，避免设置页膨胀，确有分化需求再拆；辅助调用（低频、无特殊能力要求）跟随 `default` 不单独设槽。

## 7. 前端设计

### 7.1 模型管理设置分区

`frontend/src/components/settings/ModelManagementSection.jsx`（由 `SettingsDialog.jsx` 组装）：

- **档案列表**：卡片式增删改（名称 / base_url / api_key / model / 能力勾选 + 可选覆盖项折叠区，默认继承全局）；api_key 展示用 `***xxxx` 掩码，保存遵循既有掩码协议（不落盘不外发）；
- **槽位指派**：每角色一个下拉框，选项按能力过滤（OCR 槽只列出含 vision 的档案），提供「跟随 default」空选项；
- 被任一槽位引用的档案禁用删除入口（后端 assignments 引用校验兜底）；
- 新增页面文字补齐 i18n 三语。

### 7.2 功能处显示当前模型

各 LLM 功能入口（OCR 上传区、逐段检查页、Agent 页、写作聊天侧栏等）展示当前解析模型徽标（`ModelBadge.jsx`），数据源 `GET /api/llm/routes`，进入页面时拉取；点击跳转设置对话框对应分区。多用户模式下同样展示。

## 8. 风险与已裁决事项

- **不同 provider 的 OpenAI 兼容程度差异**（工具调用格式、思维链字段命名、参数白名单）是最大不确定点：封装层统一适配 + 档案级 `extra_body` 逃生口；
- **上下文窗口探测按档案隔离**，避免跨档案串味（§3）；
- **已裁决**：档案删除被槽位引用 → 前端禁删 + 后端保存校验双保险；编辑器助手端点 → 跟随 `writing_chat` 槽不单开；`writing_chat.summary_model`（会话压缩模型覆写）→ 覆写模型名而非档案，行为保持不变。
