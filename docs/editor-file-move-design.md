# 网页编辑器文件树拖拽移动设计（移动文件/文件夹、新建与删除文件夹）

> 状态：**已实施**。本文档为设计参考，记录决策基线与机制描述；行为细节
> 以代码为准（后端 `backend/modules/project_router.py` /
> `backend/modules/editor/service.py`，前端 `ProjectExplorer.jsx` /
> `useEditorBuffer.js` / `lib/editor/tabs.js` / `frontend/src/api/index.js`）。
> 关联文档：[editor.md](modules/editor.md)（编辑器模块参考）、
> [editor-design.md](editor-design.md)（文件树操作原型，决策 Q3）、
> [editor-file-upload-design.md](editor-file-upload-design.md)（文件树操作
> 与上传通路先例）。
> 代码注释引用锚点形如 `editor-file-move-design §4.1` / `决策 Q2`。

## 1. 背景与目标

实施前，文件树只有「行内按钮式」单点操作（新建文件、上传、删除、设主
文件）：目录结构只能来自 zip 导入 / git 克隆，既不能新建文件夹，也不能
调整文件归属目录，目录重组只能整包下载改完再传。本功能补齐目录组织能力：

- 拖拽移动文件 / 文件夹到任意目录（含项目根）。
- 新建文件夹，让拖拽有落点（实施前没有 mkdir 端点，目录只能来自项目导入）。
- 递归删除文件夹，让新建的目录可回收（实施前 delete 端点只支持删文件）。

## 2. 决策记录（grilling 共识）

| 编号 | 决策点 | 结论 | 理由 |
| --- | --- | --- | --- |
| Q1 | 可拖动对象 | **文件 + 文件夹** | 目录重组是核心价值；`shutil.move` 天然两者通吃；前端按路径前缀一次重映射即可覆盖子树 |
| Q2 | 目标同名冲突 | **拒绝**（409 `target_exists`） | 与 create-file 冲突语义一致；静默改名让用户找不到文件，且与引用进一步脱节 |
| Q3 | 主文件被拖走 | **允许移动并自动更新** `.latexcellence/main_file` 记录 | 「主文件不能动」是需要向用户解释的怪限制；自动更新成本低（改内部记录 + 响应带回） |
| Q4 | `\input`/`\include` 引用断裂 | **不自动改写**，写入已知限制 | 自动改写要处理扩展名省略、`\graphicspath`、注释等边角，改错比不改更危险；确认框太吵 |
| Q5 | 拖拽技术选型 | **原生 HTML5 Drag & Drop，零新依赖** | 项目已有 drag events 先例（FormatConvert / ImageOCR）；触屏不支持记为已知限制，编辑器为桌面重度场景 |
| Q6 | 已打开标签页 | **路径重映射，buffer/baseMd5/脏标记原样保留** | 唯一不丢数据的方案；删除文件的「关 tab」逻辑不能照搬 |
| Q7 | v1 增强项 | **hover 自动展开 + 新建文件夹**；多选拖拽与外部文件拖入上传不做 | 前两项直接决定可用性；后两项是独立任务，不与树内移动混在一个改动里 |
| Q8 | 行内重命名 | **不做** | 端点（from→to）天然支持，留独立小任务 |
| Q9 | 文件夹删除 | **递归删除**，前端 ConfirmDialog 强警告 | 补 mkdir 造成的「建了删不掉」缺口；用户明确接受风险，强警告兜底 |

### 派生决策（随 Q1–Q9 必然成立）

| 编号 | 结论 |
| --- | --- |
| Q10 | 递归删除目标子树内含主文件时 **400 `main_file_protected` 拒绝**（提示先移走或先取消主文件）——与既有单文件删除保护同口径；只有移动场景跟随改写记录（Q3），删除路径无需清理记录 |
| Q11 | move / create-dir / 目录删除**均不进统一撤销记账**，与 create/delete/upload 一致，靠确认框与强警告兜底 |
| Q12 | **不做乐观更新**：drop 后发请求，成功才 `setFileTree(响应新树)` + 重映射；失败仅 toast，树纹丝不动（复用响应带树的单次往返模式） |
| Q13 | URL `?file=` 与 sessionPersistence 持久化键随重映射同步改写，防止刷新后按旧路径打开 404 或丢脏缓冲区 |

## 3. 安全护栏（非协商）

1. **路径唯一入口**：`from_path` / `to_path` 双向经 `resolve_project_path()`
   （不限后缀），穿越 / 绝对路径 / null 字节 / 逐组件符号链接检测全保留；
   隐藏组件（任一路径段点开头）由 `_reject_hidden_components()` 权威拒绝，
   覆盖 `.git/`、`.latexcellence/` 等。
2. **鉴权**：JSON body 版 `require_workspace(write=True)`；viewer 403、
   未登录 401，CSRF 由安全中间件统一把关。
3. **错误消息脱敏**：面向用户的错误一律相对路径，完整路径只进服务端日志
   （多用户模式路径脱敏约束）。
4. **递归删除边界**：目标必须先通过 path_guard（项目内、非隐藏）才执行
   `rmtree`；项目根本身显式拒绝（`.` / `./` 经 path_guard 解析即根，
   400 `invalid_path`，否则目录分支会把整个项目连根删除）；物理删除无
   软删除/回收站——前端强警告为第一道闸，主文件保护为第二道（Q10）。
5. **校验顺序**：`to_path` 全部校验先于任何磁盘变更；`shutil.move` 前置
   检查全部通过后才执行，无半完成态。

## 4. 后端设计

### 4.1 API（`project_router.py`，与 create-file/delete-file 同族）

- `POST /api/project/move-file`（JSON body：`{from_path, to_path}`），
  文件与目录通用，目录迁移 = 整棵子树随迁。校验链：鉴权 → from/to 各自
  隐藏组件拒绝 + path_guard → from 存在（404 `file_not_found`，与 delete
  同码）→ to 父目录存在（400 `parent_not_found`）→ into_self（to 等于
  from 或位于 from 子树内，400 `into_self`，**先于** target_exists——
  to == from 时目标必然「已存在」，字面顺序会把拖回原位误报 409）→
  to 不存在（409 `target_exists`，同名文件与同名目录均拒绝）→
  `asyncio.to_thread` 执行 `shutil.move` → 主文件记录跟随改写（§4.2）→
  `invalidate_project_outline_cache()` → 响应
  `{status, from_path, to_path, main_file, file_tree}`。
- `POST /api/project/create-dir`（JSON body：`{dir_path}`）。名字消毒与
  create-file 同口径（空名/控制字符/分隔符/点开头拒绝，复用上传目标名
  消毒助手）→ 隐藏组件拒绝 → path_guard → 已存在同名文件或目录
  409 `target_exists` → 父目录必须已存在（400 `parent_not_found`，不做
  递归 mkdir，与 create-file 一致）→ `asyncio.to_thread` mkdir → 响应
  `{status, dir_path, file_tree}`。
- `POST /api/project/delete-file` 扩展：目标为目录时改走 `shutil.rmtree`
  递归删除（子树含主文件时按 Q10 拒绝——主文件保护从「等于目标」扩为
  「等于目标或位于目标子树内」）；项目根本身守卫见 §3-4；隐藏拒绝、
  path_guard 等其余逻辑不变。
- move 不产生新字节落盘：不调用配额预检 / 用量刷新（与上传、新建不同）。
- 不做 git mv / 自动暂存联动，git 状态由既有 git 面板如实反映。

### 4.2 主文件记录跟随

- 主文件路径存于 `.latexcellence/main_file`（隐藏路径，用户路径校验链不可
  触达）。移动主文件本身或其祖先目录时，按「旧路径前缀 → 新路径前缀」
  改写记录，复用 set-main-file 的内部写入通路（内部机制写隐藏路径不受
  `_reject_hidden_components` 约束，该拒绝只针对用户提供的路径）。
- 切片改写前先经 Path 规范化：记录会原样存储用户输入（如 `./main.tex`），
  未经规范化的前缀切片会产出目录路径，导致改写失败、记录悬空。
- 改写失败仅记警告、移动结果不回滚（与 set-main-file 持久化的容错语义
  一致）；响应携带改写后的 `main_file`，前端以响应为单一事实源。

## 5. 前端设计

### 5.1 拖拽交互（`ProjectExplorer.jsx`，原生 HTML5 DnD，Q5）

- `canWrite` 时文件 / 目录行设 `draggable`；WorkspacePage 只读用法不受影响。
- dragstart：`dataTransfer.setData(自定义 MIME, JSON {path, isDir})`，
  `effectAllowed = "move"`，被拖行半透明。
- 落点：目录行（排除自身与子孙，isDir 前缀判定）与树底部空白区（= 项目根）；
  合法落点高亮 + `dropEffect = "move"`，非法落点（文件行、自身 / 子孙）
  不响应放置且不高亮。
- 拖到折叠目录上悬停约 500ms 自动展开（Q7，展开即成不回弹）；dragleave /
  drop / dragend / 组件卸载统一取消计时器并复位全部拖拽状态（拖拽进行中
  切左栏大纲 / 离开路由时不产生卸载后 setState）。

### 5.2 移动流程与状态同步（`useEditorBuffer.js`）

- `handleMoveEntry(fromPath, targetDir, isDir)`：`to_path =
  joinProjectPath(targetDir, getBaseName(fromPath))`（`lib/editor/files.js`
  纯函数）；拖回原位静默忽略不发请求（后端 to == from 语义为 400
  into_self）；目录拖入自身子树由前端兜底拦截。
- 成功（Q6/Q12/Q13）：
  1. `remapMovedPaths()` 纯归约重映射 `tabs[].filePath`、`activeTabPath`
     与 409 冲突归属路径——buffer / savedBuffer / baseMd5 / 脏标记 /
     陈旧标记原样保留；文件夹按路径前缀一次匹配覆盖整棵子树（`/` 边界
     防误伤同名前缀目录）；无命中时状态引用不变（订阅方零触发）；
  2. 全局 `activeFile`（useStore）与只读预览覆盖层归属路径按同口径重映射
     （预览文件随移动换路重拉，未命中零触发）；
  3. `setMainFile(res.main_file)` + `setFileTree(res.file_tree)`（以响应
     为单一事实源）；
  4. 失效引用符号缓存（`\label`/`\cite` 来源路径变化）；
  5. sessionPersistence 持久化键与 URL `?file=` 由既有订阅随 tabs /
     activeTabPath 变化自动跟进改写。
- 失败：`translateError()` 翻译后 toast，树与标签页零改动。

### 5.3 新建 / 删除文件夹 UI

- 入口（Q7）：头部「新建文件夹」按钮（根目录）+ 目录行 hover FolderPlus
  按钮，与现有「新建文件」同模式。
- `CreateFileDialog` 增加目录模式（`mode = "dir"`），前端消毒预检
  （`validateNewDirName()`）与服务端同口径。
- 目录行 hover 删除按钮：ConfirmDialog 强警告（「将永久删除 ×× 及其内部
  全部文件与子文件夹，此操作不可恢复」）；仅 `canWrite` 时显示。
- 删除目录成功后，关闭 filePath 命中该目录前缀的全部 tab（扩展现有
  `handleDeleteFile` 的精确匹配关闭逻辑），只读预览覆盖层按同前缀收起。

### 5.4 API 层（`frontend/src/api/index.js`）

- `api.project.moveFile(fromPath, toPath, workspaceId)`、
  `api.project.createDir(dirPath, workspaceId)`；目录删除复用
  `api.project.deleteFile`。

### 5.5 i18n（三语同步）

- `errors` 命名空间：`target_exists` / `into_self` / `file_not_found` /
  `main_file_protected`（已有则复用）。
- `pages.editor`：`moved` / `folderCreated` / `folderDeleted` 及失败文案。
- `projectExplorer`：`createFolder` / `createFolderHere` / `deleteFolder`。
- 维护遵循 `.agents/skills/latexcellence-i18n-errors/SKILL.md` 的 key 规范
  （i18nCoverage 单测拦截漏配）；组件内不硬编码条件翻译。

## 6. 测试基线

后端 `tests/test_api/test_editor_file_ops.py` 增补：

- move：文件 → 子目录 / 根；文件夹整棵迁移（嵌套内容随迁）；409
  `target_exists`（同名文件与同名目录）；400 `into_self`（自身、子孙）；
  400 `parent_not_found`；404 from 不存在；隐藏路径 from/to 拒绝；移动
  主文件后记录跟随（含 `./` 前缀规范化回归）；移动含主文件的文件夹后
  前缀改写；无关路径记录不动；响应 `file_tree` 正确。
- create-dir：成功（根 / 子目录）、重复 409（目录名被文件占用亦拒绝）、
  父缺失 400、非法名 / 隐藏名拒绝。
- delete-file 目录扩展：递归删除成功（含空目录）；含主文件目录 400
  `main_file_protected`；隐藏目录拒绝；项目根本身 400 `invalid_path`
  回归。

多用户 `tests/test_multiuser/test_editor_file_ops.py` 增补：viewer 403、
未登录 401、CSRF、错误消息相对路径脱敏、move / create-dir 不产生配额
变化与 workspace_edit_ops 记账。

前端（node --test，`frontend/test/editorFileMove.test.mjs`）：
`remapMovedPath` / `remapMovedPaths`（精确命中 / 前缀命中且 `/` 边界防
误伤 / activeTabPath / 预览归属路径同口径 / 无关 tab 不动 / 无命中空转）、
`getBaseName` 与 `joinProjectPath` 落位拼接、`validateNewDirName` 消毒
分支、移动与新建目录错误码三语条目一一对应。

Mock 原则照旧：单元测试不发起真实网络请求。

## 7. 文档索引

实施时的文档同步义务已履行，行为描述沉淀于常设文档：
[editor.md](modules/editor.md)（move-file / create-dir API、文件树交互与
测试覆盖）、[frontend.md](frontend.md)（ProjectExplorer 组件描述）、
`PROJECT_SUMMARY.md` / `DEVELOPMENT.md`（索引）、`README.md`（用户功能
简介）。本节仅作索引，无其余义务清单。

## 8. 非目标与已知限制

已知限制（使用时需知悉）：

- `\input`/`\include` 引用不自动改写（Q4）：移动后引用由用户自行维护。
- 触屏不支持拖拽（Q5）：原生 HTML5 DnD 仅指针设备可用。
- 移动 / 删除无撤销（Q11）：不进 undo 账本，靠确认弹窗与强警告兜底；
  物理删除无回收站 / 软删除。

非目标（延后或明确不做）：

- 多选拖拽、外部文件拖入上传、行内重命名（Q7/Q8，独立任务；move 端点
  from→to 形态天然支持重命名）。
- git mv / 自动暂存联动。
