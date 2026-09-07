# 网页编辑器文件上传设计（文件树上传任意格式资源）

> 状态：**设计完成，待实施**。本文档记录实施依据与决策基线；实施完成后按方案文档生命周期改写为设计参考。
> 关联文档：[editor.md](modules/editor.md)（编辑器模块参考）、[editor-design.md](editor-design.md)（文件树操作原型，决策 Q3）。
> 代码注释引用锚点形如 `editor-file-upload-design §4.2` / `决策 D5`。

## 1. 背景与目标

### 1.1 现状

- 文件树（`frontend/src/components/ProjectExplorer.jsx`）已支持新建/删除/设为主文件，写操作全部走 hover 行内按钮，受 `canWrite` 门控。
- 项目目录内没有图片等二进制资源的上传入口，资源文件进入项目的唯一途径是整包 zip 导入（导入侧无类型白名单）。
- 文件树扫描（`backend/core/project_manager.py` `_scan_directory`）只列 `.tex/.bib/.cls/.sty/.pdf`，资源文件即使已存在于项目目录也不会出现在树中。
- 新建/删除白名单 `EDITABLE_SUFFIXES`（`backend/modules/editor/service.py`）不含资源文件——资源文件既不能新建也不能删除。
- 二进制预览通路已就绪：`/raw-file` 端点按 `PREVIEWABLE_SUFFIXES`（PDF + 常见位图）提供内联预览，前端 `isPreviewableFilePath` 分流到 `BinaryFilePreview` 渲染。
- 编译以项目根为 cwd（latexmk），资源文件放在项目内任意相对路径即可被 `\includegraphics` 引用；`\includegraphics` 补全片段与缺宏包自动补 `\usepackage{graphicx}` 通路均已存在。

### 1.2 目标

- 编辑器文件树直接上传文件到项目根或指定目录，支持一次多选、任意格式。
- 上传后的文件在树中可见（含此前 zip 导入自带的资源文件），可预览类型点击即预览，全部可删除。
- 单文件上传成功后可一键插入 `\includegraphics` 引用（含缺宏包自动补声明）。

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
4. **大小与配额**：单文件 20MB 流式截断；多用户模式落盘前 `enforce_project_quota()` 预检、落盘后 `refresh_owner_usage_after_write()` 刷新（照抄 create-project-file 接线）。
5. **鉴权**：路由依赖用 query 版 `require_workspace(write=True)`（create-file 用的是 JSON body 版，multipart 场景换 query 版）；viewer 403、未登录 401，CSRF 由安全中间件统一把关。
6. **文件名消毒**：拒绝空名、控制字符、路径分隔符；超长文件名由 OS 层兜底报错。

> 为什么任意格式是安全的：上传的危险从来不在「磁盘上存了什么字节」，而在「字节之后被怎样读回、被谁执行」。本项目回读有白名单、执行有沙箱（编译与 git 命令均经 SandboxRunner：无网络、只读 rootfs、资源限额）、路径有 path_guard，三条边界均与上传类型无关；zip 导入又已使「项目目录内存在任意文件」成为现状。

## 4. 后端设计

### 4.1 API

`POST /api/project/upload-file`（挂载于 `project_router.py`，与 create-file/delete-file 同族）

- **Request**：`multipart/form-data`——字段 `file`（文件体）；query 携带 `file_path`（目标相对路径：目录 + 文件名，根目录上传时为纯文件名）与 `workspace_id`。
- **校验链（顺序）**：大小（流式，超 20MB 即中止）→ 文件名消毒 → 隐藏组件拒绝 → `resolve_project_path`（不限后缀）→ 冲突检查 → 多用户配额预检 → 原子写盘 → 多用户用量刷新。
- **错误语义**与 create-file 对齐（复用 `_http()` 结构）：409 `file_exists`、400 `invalid_path`、400 `parent_not_found`、413 `quota_exceeded`；新增 413 `file_too_large`。错误消息一律使用相对路径（多用户脱敏），完整路径只进服务端日志。
- **响应**：`{status, file_path, file_tree}`——`file_tree` 为重扫后的整树（`LatexProject.refresh_file_tree()`），前端单次往返完成刷新（复用 create-file 模式）。
- 零 LLM、不进统一撤销记账；与 create/delete 一致调用 `invalidate_project_outline_cache()` 失效大纲缓存。

### 4.2 服务层（`backend/modules/editor/service.py`）

- 新增 `upload_project_file()`（async 编排，`asyncio.to_thread` 调同步实现）与 `_upload_file_sync()`，结构与 `create_project_file`/`_create_file_sync` 对齐。
- 大小上限：新增常量 `MAX_UPLOAD_FILE_BYTES = 20 * 1024 * 1024`；multipart 流式分块落临时文件（`tempfile`），超限即中止并清理，完整性确认后经 `os.replace()` 原子替换到目标路径（二进制不走 `write_text`，避免半写文件）。
- 文本类上传不受 `MAX_EDITABLE_CHARS` 约束（该常量是编辑缓冲区/保存路径的约束）；字节上限即唯一上传约束。
- 树扫描与删除白名单改动不修改 `EDITABLE_SUFFIXES` 本身（create-file/save-file 语义不动），改动点见下。

### 4.3 文件树扫描（`backend/core/project_manager.py` `_scan_directory`）

- 文件节点的收录条件从「后缀白名单」改为「非隐藏 + 非编译产物」；目录仍递归全收。
- 新增生成产物后缀黑名单常量，匹配用 `name.endswith(tuple)`（而非 `entry.suffix`），天然覆盖 `.synctex.gz`、`.run.xml` 等双后缀。黑名单覆盖 latexmk/TeX/bibtex/makeindex/glossaries/beamer 常见产物，如：`.aux .log .toc .lof .lot .out .bbl .blg .bcf .run.xml .spl .fls .fdb_latexmk .synctex.gz .xdv .dvi .nav .snm .vrb .idx .ind .ilg .glo .gls .glstex .acn .acr .alg .ist .maf`（实施时以此为基线增补；`.bak` 备份后缀一并纳入）。
- 同步更新函数 docstring（现文档写明仅收录五类后缀）。
- 行为影响：黑名单产物不出现在树中，但仍随项目下载/打包；白名单时代的现状（`.pdf` 可见）保留。

### 4.4 删除白名单（`backend/modules/editor/service.py` `_delete_file_sync`）

- `resolve_project_path` 调用不再传 `allowed_suffixes=EDITABLE_SUFFIXES`，改为不限后缀；隐藏路径被 §3-3 的组件检查与树层天然排除（隐藏项不进树、无删除入口）。
- 主文档保护（400 `main_file_protected`）与 `get_main_file` 比对逻辑不变。
- 附带效果：产物可通过 API 删除（树中不可见、无 UI 入口），为未来「清理编译产物」功能预留底层能力，本次不做 UI。

### 4.5 保持不动的部分

- `/raw-file` 端点、`PREVIEWABLE_SUFFIXES`、`_RAW_MEDIA_TYPES`。
- create-file/save-file 的 `EDITABLE_SUFFIXES`、`MAX_EDITABLE_CHARS`、`check_latex_corruption` 校验链。

## 5. 前端设计

### 5.1 `ProjectExplorer.jsx`

- props 扩展：`onUploadFile(dirPath | null, files: FileList)`；由 `EditorPage` 在 `canWrite` 时传入，工作台页只读用法不受影响。
- 入口：根标题行 Upload 图标按钮（紧邻现有 `+`）；目录行 hover Upload 按钮（紧邻目录行 `+`）。组件内置隐藏 `<input type="file" multiple>` 由按钮触发，上传中呈 loading 态防重复提交。
- 图标：`IMAGE_FILE_RE` 命中 → Image 图标（现状保留）；其余非可编辑文本类型 → 通用 File 图标（现状未知类型不进树，此为新增分支）。

### 5.2 上传流程（`frontend/src/hooks/useEditorBuffer.js`）

- 新增 `handleUploadFiles(dirPath, files)`：串行逐个调用 `api.project.uploadFile()`；每次成功 `setFileTree(res.file_tree)`（末次即最终态，与 `handleCreateFile` 模式一致）；失效引用符号缓存（上传 `.tex/.bib` 会影响 `\ref/\cite` 补全）。
- 失败收集：部分成功时 toast 汇总失败文件名与原因（经 `translateError()` 翻译），已成功文件保留。
- 单文件成功 → toast 带「插入引用」动作（D4）；上传中禁用入口按钮。

### 5.3 插入引用（`EditorPage.jsx` 插入管线）

- 复用现有 `includegraphics` 命令条目模板 `\includegraphics[width=$1\textwidth]{$2}$0`，`$2` 预填 D8 口径的相对路径；走既有 `snippetInsert` 占位符 Tab 跳转通路（与命令面板同一插入管线）。
- 无激活文本 tab 时仅提示上传成功、不可插入。
- 缺 `graphicx` 时复用 `notifyMissingPackage` 自动补宏包通路（`latex_package_rules` 已有 `includegraphics → graphicx` 映射）。

### 5.4 预览与打开

- `PREVIEWABLE_SUFFIXES` 内类型：点击 → `requestOpenFile` 现有 `isPreviewableFilePath` 分支 → `BinaryFilePreview`（零改动）。
- 其余类型：`isPreviewableFilePath` else 分支新增 D12 的 toast 提示。
- 已知边界：超过前端可编辑大小上限的文本文件（如超大 `.tex`）可存在、可编译引用，但无法在编辑器中打开（前端 `lib/editor/files.js` 现有约束，与 zip 导入行为一致）。

### 5.5 API 层与纯函数

- `frontend/src/api/index.js`：`api.project.uploadFile(filePath, file, workspaceId)` → `requestForm("project/upload-file", formData)`（credentials/CSRF/authHeaders 复用现有封装）。
- `frontend/src/lib/editor/files.js`：新增上传文件名消毒纯函数（拒绝空名/控制字符/分隔符/点开头）与相对路径拼接函数，供选择框回调预检；服务端校验仍为权威。

### 5.6 i18n

- `zh.json` / `zh-TW.json` / `en.json` 三语同步新增条目（`errors` + `components` 命名空间）：`file_too_large`（后端新增错误码必须进 errors 命名空间）、`file_exists`、`parent_not_found`、上传失败、目标目录不存在、该类型不支持预览、插入引用、已上传提示等。禁止组件内硬编码条件翻译，统一走 `errorMessages.js` + i18n。

## 6. 测试计划

- `tests/test_api/test_editor_file_ops.py`：上传成功（含任意后缀如 `.xyz`，树含新文件）；单文件超限 413；同名冲突 409；父目录缺失 400；隐藏目录/点开头文件名/控制字符文件名拒绝；产物黑名单不进 `file_tree`（含 `.synctex.gz` 双后缀命中）；删除任意非隐藏后缀成功；主文档删除保护回归；raw-file 对非白名单后缀仍拒绝。
- `tests/test_multiuser/test_editor_file_ops.py`：配额预检超限 413；写后用量刷新；viewer 403；CSRF/未登录 401。
- 前端（`frontend/test/`，`node --test`）：`editorFiles` 新增消毒/路径拼接用例。分片上传队列（`uploadQueue`）无需改动——单文件 ≤20MB 串行直传。
- 遵循既有 Mock 原则：单元测试不发起真实网络请求。

## 7. 文档同步（实施时执行）

- `docs/modules/editor.md`：文件树操作章节补「上传」小节（API、白名单变化、产物黑名单）。
- `docs/backend-core.md`：`project_manager` 文件树扫描行为变更处同步。
- `PROJECT_SUMMARY.md`：目录树 docs 条目补本文件；editor 模块速查行与 `editor/service.py` 行内描述补 upload-file 能力。
- `DEVELOPMENT.md`：核对既有文档清单后补本文件条目。
- `README.md`：文件树上传属面向用户的功能点，补一句简介。
- 本文档状态头改「已实施」，按方案生命周期删去过程性内容、保留决策表与机制描述。

## 8. 非目标

- 拖拽上传、编辑器工具栏上传入口（按需追加）。
- 编译产物「一键清理」UI（本次只完成底层删除能力放开）。
- `.eps`/`.svg` 的预览转换管线（沿用 raw-file 现有排除理由）。
- 上传进度条/分片直传（单文件 20MB 内串行直传足够）。
- `\graphicspath` 目录约定或「上传到 figures/」引导（项目无此约定，保持无约定现状）。

## 9. 范围外待办（独立事项，不混入本次改动）

- **zip/7z/rar 解压层过滤 `.git`**（与 `.latexcellence/`）：本次上传入口会拒绝隐藏目录，但压缩包导入路径（`backend/core/multiuser/upload.py`）目前不滤 `.git`，含恶意 `.git/hooks` 的压缩包可进项目。docker 沙箱模式下 git 命令危害被容器隔离（无网络、只读 rootfs），local 回落模式则在宿主执行 git——建议独立小改动收口，两条入口统一拒绝隐藏目录。
