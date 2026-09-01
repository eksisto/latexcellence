# 模板库模块 `template_library/`

内置模板 + 用户私有模板库的统一管理入口（`/api/templates`）。提供模板浏览/搜索、zip 上传建库、从模板一键创建项目、当前项目另存为模板与 template_gen 生成结果入库。**零 LLM 依赖**——全部为确定性文件操作与配额/安全接线。

## 模块分层

遵循 `router.py`（薄路由）→ `service.py`（业务编排）→ 两个纯同步存储层（经 `asyncio.to_thread` 调度）的结构：

| 文件 | 职责 |
| --- | --- |
| `router.py` | 薄路由：参数校验、双模式认证、状态码映射；挂载 `/api/templates` |
| `service.py` | 业务编排：列表合并、上传建库、创建项目、另存为/生成入库、清洗与配额接线、错误统一映射 HTTPException |
| `models.py` | Pydantic v2 契约：`TemplateManifest` / `TemplateSummary` / `TemplateDetail` 与各请求模型 |
| `builtin_store.py` | 内置模板发现（实时扫描 `builtin_templates/` 读 manifest）+ 共享文件原语（id 校验、文件树枚举、path_guard 解析） |
| `user_store.py` | 用户模板库 CRUD（多用户/单用户双 root 注入；uuid4 模板 id 服务端生成） |
| `builtin_templates/` | 内置模板包内资源（随代码分发，见下文「内置模板清单」） |

## 双模式数据流

与项目/配额体系一致的双模式语义，由路由层 `_current_actor` 依赖注入决定：

- **多用户模式**：强制登录（未登录 401）。用户库根目录为 `<users_root>/<user_id>/template_library/<template_id>/`，目录归属由路径天然保证——只能访问内置模板与自己的模板，他人模板一律 404（不泄露存在性）。会话类资源（`from-template-gen`）的输出根经 `userspace.user_resource_root("templates", ...)` 按用户隔离。
- **单用户模式**：`actor=None`，走全局库目录 `features.template_library.user_library_dir`（默认 `data/template_library/`），保持存量单用户行为。

模板 `kind`（builtin/user）由存放位置判定，不写入 manifest；API 视图模型（`TemplateSummary`）始终携带。

内置模板发现**不做启动期缓存**：每次请求实时读盘扫描 manifest，与配置热重载/模板热更新语义一致；损坏或缺失 manifest 的目录跳过并告警，不中断发现。

## manifest schema

`manifest.json` 由 `TemplateManifest` 严格校验（`extra="forbid"`），字段与内置模板磁盘文件精确一致，写盘按固定字段顺序（便于 diff 审阅）：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | str | 内置模板为目录名；用户模板为服务端生成的 uuid4 hex（入库时覆盖请求值） |
| `name` | str | 模板名（入库前经 `sanitize_template_name()` 清洗：去路径分隔符/控制字符、压空白、限长） |
| `name_en` | str | 英文名（可空；繁中界面复用简中） |
| `description` | str | 用途描述 |
| `category` | enum | 固定分类枚举：paper / thesis / report / slides / resume / book / poster / notes / other（设计决策，不得扩展） |
| `main_file` | str | 主文件相对路径（缺失时自动探测：根下 `main.tex` → 含 `\documentclass` 的最浅 `.tex` → 最浅 `.tex`） |
| `engine` | str\|null | `xelatex` / `pdflatex` |
| `author` | str | 作者（入库来源为登录用户时取用户名） |
| `license` | str | 许可说明 |
| `source` | enum | `builtin` / `upload` / `template_gen` / `project_save`（入库来源） |
| `created_at` | str | ISO 日期字符串（入库当天） |

入库元数据优先级：请求显式提供 > 源目录已有 manifest > 自动回退（zip 名/项目名为名、other 分类、自动探测 main_file）。

## API 路由

Prefix: `/api/templates`, Tag: `template_library`。`features.template_library.enabled=false` 时端点整体关闭（404，不泄露存在性）；多用户模式走既有会话认证，单用户放行。

| 方法 | 路径 | 功能 |
| --- | --- | --- |
| GET | `/` | 模板列表（内置 + 我的），`category` 可选筛选 |
| GET | `/{template_id}` | 详情 + 文件树（相对路径 + 大小） |
| GET | `/{template_id}/file?path=` | 模板内源码查看（文本扩展名白名单） |
| GET | `/{template_id}/thumbnail` | 内置模板缩略图（仅内置；inline `FileResponse`） |
| GET | `/{template_id}/preview` | 内置模板 PDF 预览（仅内置） |
| POST | `/` | 上传 zip 建模板（multipart：`archive` + 可选 `name`/`category`） |
| PUT | `/{template_id}` | 改名/分类/描述（部分更新；仅自己的模板） |
| DELETE | `/{template_id}` | 删除自己的模板（内置只读） |
| POST | `/{template_id}/create-project` | 从模板创建项目（见下文） |
| POST | `/from-project` | 当前项目另存为模板（`workspace_id` 定位，复用 workspace 访问依赖） |
| POST | `/from-template-gen` | template_gen 会话产物入库（`session_id` + `name` + 可选 `category`/`description`） |

### 从模板创建项目（create-project）

纯复制模板文件树为新项目，无变量替换；项目名由用户输入。复制前按清洗规则剔除库元数据与编译产物。

- **多用户**：配额预检（项目空间，超限 413，项目不落位）→ `move_into_user_space` 落位（失败清理残留）→ `create_project_row` 建行 → `schedule_auto_init` 异步初始化 Git——与上传创建完全同一条链路，同名自动去重。响应 `{project: {project_id, name}}`。
- **单用户**：`{name, target_dir}`，创建 `<target_dir>/<项目名>/`；目标已存在且非空报 409。响应 `{project: {name, path}}`。

前端创建成功后跳转 `/workspace`：多用户对齐项目工作台的打开模式（清空项目态 → 置项目身份 → 由工作台恢复链路完成打开，含主文档段落拉取与角色同步）；单用户以新 `project_path` 触发既有恢复链路。

### 入库通道（upload / from-project / from-template-gen）

三者共用 `_ingest_from_directory()`：清洗复制源目录到暂存区 → 构造 manifest → 衍生配额校验（多用户，超限 413）→ 复制入库 → 刷新用量缓存。差异仅在源与额外排除：

| 通道 | 源 | 额外排除 |
| --- | --- | --- |
| `POST /`（upload） | zip 安全解压结果 | 无扩展名杂项文件（`.gitignore`/`Makefile` 等，解压即清理，见「安全」） |
| `POST /from-project` | 当前打开项目的根目录 | 清洗规则（编译产物 + `.git/`） |
| `POST /from-template-gen` | template_gen 会话沙箱 | 清洗规则 + `trace.jsonl` + `.pdf`（PDF 编译输出） |

## 安全边界

- **上传解压**：复用 `backend/core/multiuser/upload.py` 的 `extract_archive` 安全链（zip 炸弹/路径穿越/嵌套深度防护），解压预算取当前用户「模板」类别剩余衍生配额（单用户退化为上传大小上限），超限即时拒绝；上传包流式写盘并实时校验 `max_upload_mb`。仅接受 `.zip`。
- **扩展名白名单**：上传模板包内出现 `allowed_extensions`（LaTeX 相关收窄，含图形/字体素材）之外的文件类型直接拒绝。无扩展名文件（`Path.suffix` 为空串，如 `.gitignore`/`Makefile`/`LICENSE`）豁免校验，但视为上传包冗余：解压后由 `_remove_extensionless_files()` 统一清理（连同清空后的子目录），不入库。
- **路径安全唯一入口**：模板内文件读取统一经 `builtin_store.resolve_template_file()` → `path_guard.resolve_project_path()`（拒绝绝对路径 / `..` / 符号链接 / 越界逃逸 / 白名单外扩展名）；模板 id 经 `SAFE_ID_RE`（字母/数字/下划线/连字符）校验，杜绝路径穿越。
- **源码查看白名单**：`file` 端点是纯文本通道，仅放行 `.tex/.sty/.cls/.bib/...` 等文本扩展名；二进制素材走 `thumbnail`/`preview` 专用端点。
- **路径脱敏**：API 响应与错误消息只携带模板内相对路径，服务器绝对路径只进服务端日志（对齐 AGENTS.md 多用户路径脱敏约束）。
- **权限不泄露**：他人模板/不存在的模板统一 404；内置模板改名/删除返回 `builtin_readonly`。

## 配额

- **创建项目**走项目空间配额：多用户复制前 `enforce_project_quota` 预检，超限 413（与上传创建同口径）。
- **模板库本体**占衍生配额：`<users_root>/<user_id>/template_library/` 已加入 `backend/core/multiuser/quota.py` 现有「模板」类别（templates sub）扫描范围，上传/另存为/生成入库均按入库体量做 `enforce_derived_quota_for_current_user(size, "templates")` 校验（超限 413），入库成功后 `checkpoint_derived_usage_for_current_user` 刷新缓存。工作台分类用量条与自助清理零改动。
- 单用户模式无配额语义。

## 配置项（`features.template_library`）

| 字段 | 默认 | 说明 |
| --- | --- | --- |
| `enabled` | `true` | 模块总开关（false 时 `/api/templates` 整体 404，前端隐藏全部入口） |
| `user_library_dir` | `data/template_library/` | 单用户模式模板库目录（多用户按用户空间隔离） |
| `max_upload_mb` | `20` | 上传模板 zip 大小上限 |
| `allowed_extensions` | LaTeX 相关清单 | 上传包内允许保存的文件扩展名白名单（`.tex/.sty/.cls/.bib/...` + 图形 + 字体） |

## 清洗规则

清洗的唯一实现在 `service._make_clean_ignore()`（`shutil.copytree` 的 ignore 回调），创建项目与另存为/生成入库共用：

- 一律排除：`.git/`；LaTeX 编译产物扩展名（`.aux/.log/.out/.toc/.bbl/.fls/.fdb_latexmk/.synctex.gz/...`）
- 创建项目额外排除 `manifest.json`（内置模板的 `assets/` 元资源目录一并排除）——库工件不混入新建项目
- `from-template-gen` 额外排除 `trace.jsonl` 与 `.pdf`
- 上传模板例外：不走 `_make_clean_ignore()`，而是解压后清理无扩展名杂项文件 + 全包扩展名白名单收窄（白名单外文件直接拒绝）

## 内置模板清单

内置模板位于 `backend/modules/template_library/builtin_templates/`，全部原创编写、仅用 TeX Live 自带宏包（中文走 ctex 默认 fandol 字体方案），目录约定为 `<template_id>/manifest.json + assets/（thumbnail.png + preview.pdf 元资源）+ 根下 LaTeX 工程文件`：

| 目录名 | 名称 | 分类 | 用途 |
| --- | --- | --- | --- |
| `blank` | 空白项目 | other | 最简可编译的空白文档，从零开始自由写作 |
| `paper-cn` | 中文论文（ctexart） | paper | 中文学术论文：摘要、关键词、图表公式与参考文献，单栏版式 |
| `paper-en` | 英文论文（IEEE 双栏） | paper | IEEE 风格英文双栏会议/期刊论文 |
| `thesis-cn` | 中文学位论文 | thesis | 本科毕业论文框架：封面、声明、中英文摘要、目录、章节、参考文献与致谢 |
| `book-cn` | 中文书籍/讲义 | book | ctexbook 结构：扉页、前言、分篇章节、定理环境、习题与附录 |
| `report-cn` | 中文课程/实验报告 | report | 封面、评分表与目的/原理/步骤/结果分析章节 |
| `slides-beamer` | 中文幻灯片（Beamer） | slides | Beamer 中文演示：自绘简洁主题、分节目录、要点、图表与总结页 |
| `resume-cn` | 中文简历 | resume | 中文单页简历：教育背景、实习/项目经历、技能与荣誉 |
| `resume-en` | 英文简历（现代风） | resume | 自绘样式的英文单页简历，无第三方模板依赖 |
| `poster-a0` | A0 学术海报 | poster | A0 竖版三栏学术海报，色块分节标题 |

每个模板均带预编译元资源（`assets/thumbnail.png` 卡片缩略图 + `assets/preview.pdf` 详情页 PDF 预览）；用户模板仅文件树 + 源码查看。详情对话框中的 PDF 预览支持全屏（左下角悬浮「全屏查看」进入、覆盖层按钮或 Escape 退出，与 git-diff 页全屏预览同款设计；覆盖层经 portal 挂到 body，避免对话框动画 transform 影响 fixed 定位）。

## 维护者脚本

`tools/builtin_templates/verify_templates.py`（不进入运行时，需本机 TeX Live；生成缩略图额外依赖 pdftoppm）：

```bash
python tools/builtin_templates/verify_templates.py                 # 验证全部模板可编译
python tools/builtin_templates/verify_templates.py --gen-assets    # 验证并回填 assets/ 元资源
python tools/builtin_templates/verify_templates.py --only thesis-cn # 只验证指定模板
```

脚本把模板复制到临时目录（剔除 `assets/` 与 `manifest.json`）后按 manifest 声明的引擎用 latexmk 编译，模拟「从模板创建项目」的干净文件树；全部成功退出码 0，可接入 CI。新增/修改内置模板后必须运行验证并重新生成元资源。

## 前端

- 页面：`pages/TemplateCenterPage.jsx`（`/templates` 模板中心：搜索 + 分类 chip + 「内置模板/我的模板」tab + 卡片网格；功能关闭时空态兜底）。
- 组件：`components/template-center/`（TemplateCard、TemplateDetailDialog、TemplateUploadDialog、TemplateEditDialog、CreateProjectDialog、SaveAsTemplateDialog、SaveTemplateGenDialog、TemplateThumbnail）。
- 入口（`template_library` 开关关闭时全部隐藏）：欢迎页功能目录、项目工作台「从模板新建」、单用户工作区空态「从模板创建」、工作区菜单与编辑器工具栏「另存为模板」、模板生成页「存入模板库」。
- API 封装：`api/index.js` 的 `templateLibrary` 接口组；错误码在 `errorMessages.js` 的 `EXACT_MAP` 收录并进三语 `errors` 命名空间。

## 与 template_gen 的关系

`template_gen`（自主 Agent 生成模板）产出会话沙箱；本模块的 `POST /from-template-gen` 把会话产物清洗入库为「我的模板」（剔除 `trace.jsonl` 与 PDF 编译输出）。前端 `TemplateGenPage` 在生成完成后提供「存入模板库」按钮（弹窗确认模板名与分类，成功后 toast 提供跳转模板中心入口），会话归属的单用户/多用户差异由后端经 `userspace.user_resource_root` 处理，前端只传 `session_id`。

## 测试位置

`tests/test_template_library/`：模型校验（`test_models.py`）、内置模板发现与文件原语（`test_builtin_store.py`）、用户库 CRUD（`test_user_store.py`）、API 集成（`test_api_templates.py`）与多用户隔离/配额（`test_api_templates_multiuser.py`）。
