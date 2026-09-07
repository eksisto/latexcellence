# 网页编辑器文件上传设计（文件树上传任意格式资源）

> 状态：**已实施**。本文档为设计参考，记录决策基线与机制描述；行为细节
> 以代码为准（后端 `backend/modules/project_router.py` /
> `backend/modules/editor/service.py` / `backend/core/project_manager.py`，
> 前端 `ProjectExplorer.jsx` / `useEditorBuffer.js` / `EditorPage.jsx`）。
> 关联文档：[editor.md](modules/editor.md)（编辑器模块参考）、[editor-design.md](editor-design.md)（文件树操作原型，决策 Q3）。
> 代码注释引用锚点形如 `editor-file-upload-design §4.2` / `决策 D5`。

## 1. 背景与目标

文件树最初只列 `.tex/.bib/.cls/.sty/.pdf` 后缀：资源文件进入项目的唯一
途径是整包 zip 导入（导入侧无类型白名单），没有上传入口，资源文件也不
出现在树中、不可删除。而编译以项目根为 cwd（latexmk），资源文件放在
项目内任意相对路径即可被 `\includegraphics` 引用，二进制预览通路
（raw-file 白名单 + 前端分流）与缺宏包自动补声明也早已就绪——缺的只是
一条上传通路与配套的树展示口径。本功能补齐该通路：

- 编辑器文件树直接上传文件到项目根或指定目录，支持一次多选、任意格式。
- 上传后的文件在树中可见（含此前 zip 导入自带的资源文件），可预览类型
  点击即预览，全部可删除。
- 单文件上传成功后可一键插入 `\includegraphics` 引用（含缺宏包自动补
  声明）。

## 2. 决策记录

| 编号 | 决策点 | 结论 | 理由 |
| --- | --- | --- | --- |
| D1 | 上传类型 | **任意格式** + §3 护栏 | zip 导入已允许任意类型进项目目录，写侧类型白名单不构成实质安全边界；真正的边界在回读（raw-file）与执行（编译沙箱）侧，二者均已独立设防。文本后缀（`.tex/.bib/.sty/.cls`）一并涵盖；上传**不走** `check_latex_corruption`（该校验面向 LLM 输出的 JSON 损坏特征，用户自有文件与 zip 导入口径一致） |
| D2 | 树联动行为变化 | 接受 | 扩展树扫描后，旧项目 zip 自带的资源文件开始出现在树中；树展示项目真实内容是更正确的行为，且预览通路现成 |
| D3 | 上传入口 | 根标题行 + 目录行 hover「上传」按钮（与新建 `+` 同模式），系统文件选择框**多选** | 与现有交互语言一致，成本最小；拖拽与工具栏入口列入非目标 |
| D4 | 上传成功动作 | **单文件**成功 toast 带「插入引用」按钮；多文件仅汇总提示 | 插入是「刚好要引用它」的单文件场景，批量插入没有合理语义 |
| D5 | 同名冲突 | **拒绝**（409 `file_exists`，与 create-file 同语义） | 静默覆盖丢数据；自动重命名导致插入路径与预期不符 |
| D6 | 单文件大小上限 | **20MB 后端常量**，不引入配置项 | 对图片/PDF 图充足；避免为小功能付出配置项 + 设置页 + 首页点线目录的同步成本；多用户另有项目配额预检兜底 |
| D7 | 树展示策略 | **非隐藏全量展示 + 编译产物后缀黑名单** | 全量无黑名单会让每次编译后的产物刷屏；按类型白名单则「传了不显示」与 D1 冲突。产物仍真实存在于项目（可引用、随下载），只是不在树中显示。`.pdf` 不进黑名单（保留编译产物 PDF 可预览的现状） |
| D8 | 插入路径口径 | **相对项目根、正斜杠** | graphics 路径相对编译 cwd（主文件所在项目根），与当前编辑文件位置无关 |

### 派生决策（随 D1–D8 必然成立）

| 编号 | 结论 |
| --- | --- |
| D9 | 删除白名单放开为**任意非隐藏后缀**（主文档保护不变）——任意格式可传则必然要可删 |
| D10 | 上传**不进统一撤销记账**，与 create-file/delete-file 一致，靠成功提示兜底 |
| D11 | 端点形态为 multipart + query：multipart 无法携带 JSON body，`file_path` 与 `workspace_id` 走 query 参数 |
| D12 | 未知类型点击树条目 → toast「该类型不支持预览」，不做静默无响应 |

## 3. 安全护栏（任意格式成立的前提，非协商）

1. **回读边界不变**：`/raw-file` 的 `PREVIEWABLE_SUFFIXES` 与 `_RAW_MEDIA_TYPES` **保持现状**——任意上传内容只能「存在」，不能「被服务回浏览器」（`.svg`/`.html` 继续排除，防存储型 XSS）；前端 `isPreviewableFilePath` 双重门控同步保留。
2. **路径边界不变**：写路径唯一入口 `resolve_project_path()`（拒穿越/绝对路径/符号链接/空与 null 字节，逐组件符号链接检测），上传不传 `allowed_suffixes` 即不限后缀，其余校验全部保留。
3. **隐藏路径拒绝**：目标 `file_path` 任一组件以 `.` 开头即拒绝（覆盖 `.git/`、`.latexcellence/` 等），同时拒绝点开头的文件名。服务端权威校验 + 客户端同名消毒预检。
4. **大小与配额**：单文件 20MB 流式上限（超限即中止并清理暂存，413 `file_too_large`）；多用户模式落盘前 `enforce_project_quota()` 预检、落盘后 `refresh_owner_usage_after_write()` 刷新（照抄 create-project-file 接线）。
5. **鉴权**：路由依赖用 query 版 `require_workspace(write=True)`（create-file 用的是 JSON body 版，multipart 场景换 query 版）；viewer 403、未登录 401，CSRF 由安全中间件统一把关。
6. **文件名消毒**：拒绝空名、控制字符、路径分隔符；超长文件名由 OS 层兜底报错。

> 为什么任意格式是安全的：上传的危险从来不在「磁盘上存了什么字节」，而在「字节之后被怎样读回、被谁执行」。本项目回读有白名单、执行有沙箱（编译与 git 命令均经 SandboxRunner：无网络、只读 rootfs、资源限额）、路径有 path_guard，三条边界均与上传类型无关；zip 导入又已使「项目目录内存在任意文件」成为现状。

## 4. 后端设计

### 4.1 API

`POST /api/project/upload-file`（挂载于 `project_router.py`，与 create-file/delete-file 同族）

- **Request**：`multipart/form-data`——字段 `file`（文件体）；query 携带 `file_path`（目标相对路径：目录 + 文件名，根目录上传时为纯文件名）与 `workspace_id`（决策 D11；鉴权依赖相应使用 query 版 `require_workspace(write=True)`）。
- **校验链（顺序）**：大小（流式，超 `MAX_UPLOAD_FILE_BYTES` 即中止并清理暂存）→ 文件名消毒 → 隐藏组件拒绝 → `resolve_project_path`（不限后缀）→ 冲突/父目录检查 → 多用户配额预检（按实际上传字节数，先于落盘）→ 原子写盘 → 多用户用量刷新 → 失效大纲缓存。
- **错误语义**与 create-file 对齐（复用 `_http()` 结构）：409 `file_exists`、400 `invalid_path`（含文件名消毒与隐藏组件拒绝）、400 `parent_not_found`、413 `quota_exceeded`、413 `file_too_large`；工作区未绑定项目 400 `invalid_workspace`。错误消息一律使用相对路径（多用户脱敏），完整路径只进服务端日志。
- **响应**：`{status, file_path, file_tree}`——`file_tree` 为重扫后的整树（`LatexProject.refresh_file_tree()`），前端单次往返完成刷新（复用 create-file 模式）。
- 零 LLM、不进统一撤销记账（D10）；与 create/delete 一致调用 `invalidate_project_outline_cache()` 失效大纲缓存。

### 4.2 服务层（`backend/modules/editor/service.py`）

- 入口 `upload_project_file()`（async 编排，各阻塞步骤经 `asyncio.to_thread` 调度），按校验链拆为三个同步助手：`_stream_upload_to_temp()`（multipart 流式分块落系统临时文件，逐块累计字节、超限立即中止并清理）、`_prepare_upload_sync()`（消毒 → 隐藏组件拒绝 → path_guard → 冲突/父目录检查，返回目标绝对路径）与 `_commit_upload_sync()`（原子落盘）。暂存文件在整条校验链的 finally 中统一清理，任何一步失败都不残留。
- 原子落盘细节：系统临时目录与项目目录可能跨文件系统，直接 replace 会 EXDEV 失败——与 `file_ops.atomic_write` 同模式，先复制到目标同目录临时文件，再 `Path.replace()`（os.replace 原子语义）替换到目标路径；整条链路按字节搬运，二进制不走 `write_text`。
- 大小口径：`MAX_UPLOAD_FILE_BYTES = 20 * 1024 * 1024`（后端常量，不引入配置项）是上传的唯一大小约束；文本类上传不受 `MAX_EDITABLE_CHARS` 约束（该常量是编辑缓冲区/保存路径的约束）；多用户模式另有项目配额预检兜底。
- 树扫描与删除白名单改动不修改 `EDITABLE_SUFFIXES` 本身（create-file/save-file 语义不动），改动点见 §4.3 / §4.4。

### 4.3 文件树扫描（`backend/core/project_manager.py` `_scan_directory`）

- 文件节点收录条件为「非隐藏 + 非编译产物」（决策 D2/D7）：任意格式资源（zip 导入或文件树上传）均在树中可见；目录仍递归全收。
- 编译产物黑名单常量 `BUILD_ARTIFACT_SUFFIXES`，匹配用 `name.endswith(tuple)`（而非 `entry.suffix`），天然覆盖 `.synctex.gz`、`.run.xml` 等双后缀；覆盖 latexmk/TeX/bibtex/makeindex/glossaries/beamer 常见产物，`.bak` 备份后缀一并纳入（完整清单以代码为准）。
- 行为影响：黑名单产物不出现在树中，但仍真实存在于项目、可被引用、随项目下载/打包；`.pdf` 刻意不进黑名单，保留编译产物 PDF 可见可预览的现状。

### 4.4 删除白名单（`backend/modules/editor/service.py` `_delete_file_sync`）

- `resolve_project_path` 调用不传 `allowed_suffixes`，即不限后缀（决策 D9：任意格式可传则必然要可删）；隐藏路径由 `_reject_hidden_components()` 权威拒绝（与上传路径共用同一实现），不依赖「不进文件树、无删除入口」的树层排除。
- 主文档保护（400 `main_file_protected`）与 `get_main_file` 比对逻辑不变。
- 附带效果：产物可通过 API 删除（树中不可见、无 UI 入口），为未来「清理编译产物」功能预留底层能力，本次不做 UI。

### 4.5 保持不动的部分

- `/raw-file` 端点、`PREVIEWABLE_SUFFIXES`、`_RAW_MEDIA_TYPES`。
- create-file/save-file 的 `EDITABLE_SUFFIXES`、`MAX_EDITABLE_CHARS`、`check_latex_corruption` 校验链。

## 5. 前端设计

### 5.1 `ProjectExplorer.jsx`

- props：`onUploadFile(dirPath | null, files: FileList)` 与 `uploading`；由 `EditorPage` 在 `canWrite` 时传入，工作台页只读用法不受影响。
- 入口：根标题行 Upload 图标按钮（紧邻现有 `+`）；目录行 hover Upload 按钮（紧邻目录行 `+`）。组件内置隐藏 `<input type="file" multiple>` 由按钮触发，`uploadDirRef` 记录触发入口对应的目标目录（null = 项目根）；上传中按钮呈 loading 态防重复提交。
- 图标：`IMAGE_FILE_RE` 命中 → Image 图标；其余非可编辑文本类型 → 通用 File 图标。

### 5.2 上传流程（`frontend/src/hooks/useEditorBuffer.js`）

- `handleUploadFiles(dirPath, files)`：串行逐个调用 `api.project.uploadFile()`（单文件 ≤ 20MB，无需分片队列）；发起前 `validateUploadFileName()` 做与服务端同口径的消毒预检，无效名不发请求；每次成功以响应携带的重扫整树 `setFileTree`（末次即最终态，与 `handleCreateFile` 模式一致）；失效引用符号缓存（上传 `.tex` / `.bib` 会引入新的 `\label` / `\cite` 来源）。
- 失败收集：部分成功时 toast 汇总失败文件名与原因（经 `translateError()` 翻译），已成功文件保留、不回滚。
- 单文件成功 → toast 带「插入引用」动作（D4；动作由页面注入的 `buildUploadInsertAction` 构造，不可引用类型 / 无激活文本 tab / 视图未就绪时返回 undefined，仅提示上传成功）；上传中禁用入口按钮。

### 5.3 插入引用（`EditorPage.jsx` 插入管线）

- `insertFigureReference()` 复用命令面板 `includegraphics` 条目模板 `\includegraphics[width=$1\textwidth]{...}$0`，路径参数预填 D8 口径的相对路径（相对项目根、正斜杠）；走既有 `snippetInsert` 占位符 Tab 跳转通路（与命令面板同一插入管线）。
- 仅位图 / PDF（与可预览白名单同集合）且存在激活文本 tab 时提供插入动作。
- 缺 `graphicx` 时复用 `notifyMissingPackage` 自动补宏包通路（`latex_package_rules` 已有 `includegraphics → graphicx` 映射）。

### 5.4 预览与打开

- `PREVIEWABLE_SUFFIXES` 内类型：点击 → `requestOpenFile` 现有 `isPreviewableFilePath` 分支 → `BinaryFilePreview`（零改动）。
- 其余类型：树条目点击与 URL `?file=` 直达两个入口在 `isPreviewableFilePath` 与 `isEditableFilePath` 均未命中时 toast 提示不支持预览（D12，`pages:editor.unsupportedType`），不做静默无响应。
- 已知边界：超过前端可编辑大小上限的文本文件（如超大 `.tex`）可存在、可编译引用，但无法在编辑器中打开（前端 `lib/editor/files.js` 现有约束，与 zip 导入行为一致）。

### 5.5 API 层与纯函数

- `frontend/src/api/index.js`：`api.project.uploadFile(filePath, file, workspaceId)` → `requestForm("project/upload-file", formData)`（`file_path` 与 `workspace_id` 以 query 携带，credentials/CSRF/authHeaders 复用现有封装）。
- `frontend/src/lib/editor/files.js`：`validateUploadFileName()`（拒绝空名/控制字符/分隔符/点开头，错误码映射 i18n key）与 `joinProjectPath()`（目录为空 = 项目根，返回文件名本身）；均为 node --test 直测纯函数，服务端校验仍为权威。

### 5.6 i18n

- `zh.json` / `zh-TW.json` / `en.json` 三语同步维护相关条目：`errors` 命名空间的 `uploadFileTooLarge` 等上传错误码（后端新增错误码必须进 errors 命名空间）、上传成功 / 失败汇总与「插入引用」文案、`projectExplorer.uploadFileError.*` 消毒预检错误码。组件内不硬编码条件翻译，统一走 `errorMessages.js` + i18n。

## 6. 测试基线

- `tests/test_api/test_editor_file_ops.py`：上传任意后缀成功（树含新文件，根/子目录）；单文件超限 413（落盘前拦截）；同名冲突 409；父目录缺失 400；隐藏目录 / 点开头文件名 / 控制字符 / 空文件名 / 反斜杠分隔符拒绝；产物黑名单不进 `file_tree`（含 `.synctex.gz` 双后缀命中，资源文件保留）；删除任意非隐藏后缀成功；主文档删除保护回归；raw-file 对非白名单后缀仍拒绝。
- `tests/test_multiuser/test_editor_file_ops.py`：owner 上传成功刷新用量且不产生 workspace_edit_ops 记账；配额预检超限 413（落盘前拦截）；viewer 上传 403；CSRF 缺失与未登录 401；错误消息路径脱敏。
- 前端（`frontend/test/editorFiles.test.mjs`，node --test）：`validateUploadFileName` 消毒分支与 `joinProjectPath` 路径拼接。
- 遵循既有 Mock 原则：单元测试不发起真实网络请求。

## 7. 文档索引

实施时的文档同步义务已履行，行为描述沉淀于常设文档：[editor.md](modules/editor.md)（上传 API、文件树操作与测试覆盖）、[backend-core.md](backend-core.md)（project_manager 树扫描口径）、`PROJECT_SUMMARY.md` / `DEVELOPMENT.md`（索引）、`README.md`（用户功能简介）。本节仅作索引，无其余义务清单。

## 8. 非目标

- 拖拽上传、编辑器工具栏上传入口（按需追加）。
- 编译产物「一键清理」UI（本次只完成底层删除能力放开）。
- `.eps`/`.svg` 的预览转换管线（沿用 raw-file 现有排除理由）。
- 上传进度条/分片直传（单文件 20MB 内串行直传足够）。
- `\graphicspath` 目录约定或「上传到 figures/」引导（项目无此约定，保持无约定现状）。

## 9. 范围外待办（独立事项，不混入本次改动）

- **zip/7z/rar 解压层过滤 `.git`**（与 `.latexcellence/`）：本次上传入口会拒绝隐藏目录，但压缩包导入路径（`backend/core/multiuser/upload.py`）目前不滤 `.git`，含恶意 `.git/hooks` 的压缩包可进项目。docker 沙箱模式下 git 命令危害被容器隔离（无网络、只读 rootfs），local 回落模式则在宿主执行 git——建议独立小改动收口，两条入口统一拒绝隐藏目录。
