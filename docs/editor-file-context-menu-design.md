# 网页编辑器文件树右键菜单设计（自定义上下文菜单、重命名与图标收敛）

> 状态：**已实施**。本文档为设计参考，记录决策与机制；行为细节以代码为准
> （前端 `ProjectExplorer.jsx` / `CreateFileDialog.jsx` / `useEditorBuffer.js` /
> `lib/editor/contextMenuItems.js` / `components/ui/context-menu.jsx`）。
> 关联文档：[editor.md](modules/editor.md)（编辑器模块参考）、
> [editor-file-move-design.md](editor-file-move-design.md)（拖拽移动先例：
> move 通路、主文件跟随、tab 重映射）、
> [editor-file-upload-design.md](editor-file-upload-design.md)（上传通路先例）。
> 代码注释引用锚点形如 `editor-file-context-menu-design §3` / `决策 Q3`。

## 1. 背景与目标

实施前，文件树的单点操作全部依赖「行内 hover 图标堆积」：目录行与文件行
各挂一排 hover 按钮，视觉密度高、显得杂乱；同时缺少本地编辑器习以为常的
右键操作入口（删除文件、在某目录下新建、设为主文件等）。本功能补齐：

- 文件树独立右键菜单（拦截浏览器原生菜单），按目标类型出菜单项。
- 新增**重命名**能力（move 端点 from→to 形态天然支持，此前无 UI 入口）。
- 收敛行内 hover 图标，右键菜单成为单点操作的主入口。

## 2. 决策记录（grilling 共识）

| 编号 | 决策点 | 结论 | 理由 |
| --- | --- | --- | --- |
| Q1 | 右键接管范围 | **仅文件树**（树节点 + 树背景空白区 + 头部标题栏）；编辑器正文与 tab 栏保持原生菜单 | 文件树是痛点所在；正文原生菜单承载复制/粘贴/浏览器扩展，接管反而损失功能；tab 栏右键留作后续独立特性 |
| Q2 | 既有图标去留 | **行内 hover 图标全删**（目录行与文件行全部）；**头部根级三键保留**（上传/新建文件/新建文件夹）作可发现性锚点；**主文件常驻实心星标保留** | 头部锚点解决可发现性；星标是状态指示不是操作，删了就不知道主文件是谁 |
| Q3 | 菜单项清单 | 按本文 §3 矩阵执行，**包含重命名**（走 move-file 同目录改名）；不加「复制相对路径」等杂项 | 重命名后端零成本、本地编辑器习惯操作；菜单保持短 |
| Q4 | 菜单实现选型 | **`components/ui/context-menu.jsx` 包装，与既有 dialog/select/tooltip 同构**。实施时选型从 `@radix-ui/react-context-menu` 更正为 **`@base-ui/react/context-menu`**（用户确认）：既有 ui 包装组件均为 base-ui 基座、src 无任何 radix 直接引用，「同构」理由实际指向 base-ui；base-ui 自带完整 ContextMenu 部件集且零新依赖 | 与既有组件同一无头 UI 体系；键盘导航、边界翻转、无障碍、触屏长按开箱即得；符合 AGENTS.md「优先 shadcn/ui」精神而不引入第二套体系 |
| Q5 | 交互语义 | a) **右键即选中**该行（目录只高亮不展开）；b) 树区内**完全拦截**原生菜单，不留 Shift+右键逃生门 | 右键选中是 VS Code 惯例；拦截行为可预期，开原生菜单可在树外区域 |
| Q6 | viewer 只读模式 | **不弹自定义菜单**，原生菜单保留 | 只读下无写操作可做，全禁用菜单徒增困惑 |

### 派生决策（随 Q1–Q6 必然成立）

| 编号 | 结论 |
| --- | --- |
| Q7 | 重命名交互形态：**复用 `CreateFileDialog` 加 `mode="rename"`**（预填原名并全选、独立校验口径、标题/按钮文案区分），不做行内重命名（VS Code 式原地 input 留未来打磨）——重命名低频，对话框与新建/删除风格一致、成本低 |
| Q8 | 右键高亮为**临时光**：菜单关闭（执行操作/Esc/点别处）即清除；树不引入持久选中概念（树当前无「选中」语义，点文件=打开 tab，常驻高亮会被误读为当前打开文件） |
| Q9 | 头部标题栏右键也弹根级菜单，与树背景空白区行为一致（同一回调挂两层） |
| Q10 | 非目标边界：a) tab 栏右键、编辑器正文右键不做；b) 移动端长按菜单不做（项目桌面向；base-ui Trigger 自带长按支持属免费行为，不专门适配）；c) 不加「刷新文件树」菜单项（操作响应自带整树刷新）；d) 重命名**允许改后缀**（含 .tex 改其他），主文件记录跟随沿用 move 既有机制 |
| Q11 | 重命名提交通路：`useEditorBuffer` 的 move 通路**泛化为 `moveEntry(fromPath, toPath, isDir, toastKeys)` 核心**（tab 重映射 / 主文件跟随 / 整树刷新逻辑不变，toast 文案 key 参数化：拖拽 `moved/moveFailed`、重命名 `renamed/renameFailed`）；拖拽壳保持 `joinProjectPath(targetDir, getBaseName(from))` 拼法；重命名经 `isRenameNoop()` 判定后直传同目录新 `to_path`。新名与旧名相同时静默关闭弹窗不发请求（与拖回原位静默忽略同语义）；失败 toast 后弹窗保留可继续改 |
| Q12 | 菜单挂载与门控：`canWrite=false` 时不挂载任何 ContextMenu（Q6 的实现面）；未开项目（空树）不弹菜单；挂载方式为树行 / 树背景 / 头部各自 `ContextMenu.Root` + 共享菜单内容组件，实现取简，不做全局单例 virtual anchor 的复杂方案 |
| Q13 | 「目标类型 → 菜单项清单」提为**纯函数**（`lib/editor/contextMenuItems.js`：`resolveNodeMenuTarget()` + `getFileTreeMenuItems()`，含主文件、根/背景等输入），便于 node --test 无 DOM 单测覆盖菜单矩阵 |

## 3. 菜单清单与交互语义

### 3.1 菜单项矩阵（Q3）

| 目标 | 菜单项（展示顺序） |
| --- | --- |
| .tex 文件（非主文件） | 设为主文件 ┃ 重命名 ┃ 删除 |
| .tex 文件（当前主文件） | 重命名 ┃ 删除（不出现「设为主文件」） |
| 其他文件（图片 / pdf / 其余格式） | 重命名 ┃ 删除 |
| 目录 | 新建文件 ┃ 新建文件夹 ┃ 上传到此处 ┃ 重命名 ┃ 删除 |
| 树背景空白区 / 头部标题栏（= 项目根） | 新建文件 ┃ 新建文件夹 ┃ 上传 |

样式约定：菜单项纯文字（不配图标），组间分隔线（纯函数清单的 `group`
递增处插线）；「删除」不做破坏色强化，风险交由既有确认弹窗兜底。

### 3.2 交互语义

1. 右键未选中行：该行临时高亮（样式同 hover 的 `bg-muted`），菜单锚定
   该行；目录只高亮不展开（Q5a）。
2. 菜单关闭（执行菜单项 / Esc / 点击别处）：高亮清除（Q8）。
3. 树区（节点行 / 背景 / 头部）完全拦截浏览器原生菜单，无逃生门（Q5b）；
   编辑器正文与 tab 栏不受影响。
4. viewer（`canWrite=false`）与空树不挂载菜单（Q6/Q12）。
5. 上传进行中（`uploading`）：「上传 / 上传到此处」菜单项禁用，其余可用
   （与既有头部按钮禁用语义一致）。

## 4. 前端设计

### 4.1 菜单基建（Q4）

- `frontend/src/components/ui/context-menu.jsx`：基于
  `@base-ui/react/context-menu`（既有依赖）的包装，导出
  ContextMenu / Trigger / Content / Item / Separator，风格对齐既有 ui
  组件（data-slot + Tailwind data 变体）。要点：
  - Trigger 默认渲染 `div`（树行容器无需 render 替换即可安全承载，避免
    行内既有 button 嵌套问题）；仅右键 / 触屏长按打开，左键点击不受影响。
  - Trigger 的 `contextmenu` 处理为 `stopEvent`（preventDefault +
    stopPropagation）：行 Trigger 嵌套在背景 Trigger 内不会双重打开；
    文档级监听兜底拦截树区内原生菜单（Q5b 天然满足）。
- **零新依赖**，`THIRD_PARTY_NOTICES.md` 无需登记（base-ui 已登记）。

### 4.2 ProjectExplorer 改造（Q1/Q2/Q5/Q8/Q9/Q12）

- **图标收敛**：移除 `RowActionButton` 组件与全部行内 hover 按钮（目录行的
  上传/新建文件/新建文件夹/删除，文件行的设主文件/删除）；主文件常驻实心
  星标（非按钮，`currentMainFile` 提示）保留；头部根级三键保留。
- **挂载**：每个节点行容器包 `ContextMenu.Root` + `Trigger`（render 合并
  行 div），树背景容器与头部标题栏各挂一个 Root（Q9/Q12），共享同一菜单
  内容组件 `FileTreeMenu`，内容按 Q13 纯函数产出的清单渲染，`onClick`
  分发给既有 handlers。
- **右键高亮**：行内 `menuOpen` 局部状态驱动行样式叠加 `bg-muted`；
  `onOpenChange(false)` 时清除（Q8）。目录右键不触发展开。
- **原生菜单拦截**：由 Trigger 的 stopEvent + 文档级监听天然完成（Q5b）；
  viewer 与空树分支不渲染任何 Root（Q12）。
- 菜单项动作全部回调既有 handlers：`onCreateFile` / `onCreateFolder` /
  `onDeleteFile`（走既有 ConfirmDialog）/ `onSetMainFile` /
  `onRequestUpload`（复用隐藏 input + `openUploadPicker(dirPath)`）/
  `onRenameEntry(path, isDir)`（§4.3，新增 prop）。

### 4.3 重命名（Q7/Q10d/Q11）

- `CreateFileDialog` 增加 `mode="rename"`：预填原名并全选；目标类型
  （文件/目录）由 `isDir` prop 传入，标题/图标/按钮文案区分；`dirPath`
  语义复用为「目标父目录」，提交回调仍返回 `joinProjectPath(dirPath,
  newName)` 即完整新路径，弹窗改动最小化。
- 校验独立口径：`validateNewDirName()` 级消毒（非空/无分隔符/点开头拒绝/
  隐藏组件拒绝），**不限** `EDITABLE_SUFFIXES` 白名单——图片、`.bib`、
  `.sty` 亦须可改名（与 move-file 后端不限后缀一致，Q10d）；目标预览行
  显示「旧路径 → 新路径」。
- `useEditorBuffer`：move 通路泛化为 `moveEntry(fromPath, toPath, isDir,
  toastKeys)` 核心（Q11），`handleRenameEntry(fromPath, newName, isDir)`
  经 `isRenameNoop()` 短路后拼同目录 `to_path` 调核心；同名静默关闭，
  失败 toast 后弹窗保留。
- 主文件跟随与 tab 路径重映射由 move 既有机制自动覆盖（响应携带改写后
  `main_file` 与重扫 `file_tree`，前端以响应为单一事实源）。
- EditorPage 接线：`renameTarget` 状态（`{path, name, isDir}`）+ 复用
  CreateFileDialog 实例（`createDialogMode === "rename"` 分流），`dirPath`
  取 `getDirName(path)`，提交回调经 `getBaseName()` 还原单段新名。

### 4.4 i18n（三语同步）

- 菜单项文案复用既有 `projectExplorer.*` key（`setMainFile` / `deleteFile`
  / `deleteFolder` / `uploadHere` / `newFileHere` / `createFolderHere`，
  原 hover 按钮 title 转正为菜单项 label）；根级菜单复用 `upload` /
  `newFile` / `createFolder`。
- 新增：`rename`（菜单项）、`renameFileTitle` / `renameDirTitle` /
  `renameDescription` / `renameNameHint` / `renameAction` /
  `renameNameError.{empty,separator,control,hidden}`、重命名成功 toast
  `pages:editor.renamed` 与失败 `pages:editor.renameFailed`。
- `zh.json` / `zh-TW.json` / `en.json` 三语同步；零后端改动故无新增
  `errors` 命名空间条目；组件内不硬编码条件翻译（遵循
  `.agents/skills/latexcellence-i18n-errors/SKILL.md`）。

## 5. 复用边界与安全（零后端改动）

- 所有菜单动作映射到既有端点：`set-main-file` / `move-file` / `delete-file`
  / `create-file` / `create-dir` / `upload-file`；鉴权（write=True JSON 版、
  上传的 query 版）、`resolve_project_path()` 路径守卫、隐藏组件拒绝、
  409 `target_exists` / 400 `main_file_protected` 等语义全部原样复用。
- 删除确认复用既有 ConfirmDialog：文件精确删除、目录递归强警告、脏 tab
  追加警示、确认后命中 tab 全部关闭。
- 错误消息一律相对路径（多用户脱敏约束不新增暴露面）；toast 经
  `translateError()` 翻译。

## 6. 非目标与已知限制

非目标（延后或明确不做）：

- tab 栏右键菜单（关闭/关闭其他等，后续独立特性）、编辑器正文右键接管
  （Q1/Q10a，正文保留原生菜单）。
- 移动端长按菜单专门适配（Q10b，项目桌面向；base-ui Trigger 自带长按
  行为不视为承诺）。
- 「刷新文件树」菜单项（Q10c）。
- 行内重命名（Q7，留未来打磨）。
- 「复制相对路径」等扩展菜单项（Q3）。

已知限制（使用时需知悉）：

- 树区内浏览器原生菜单被完全拦截（Q5b）：树区右键的原生功能不可用，
  检查元素等可经 F12 / 浏览器快捷键到达。
- 重命名/移动不自动改写 `\input`/`\include` 引用（承
  [editor-file-move-design](editor-file-move-design.md) 决策 Q4 同源限制）。
- 重命名主文件改后缀时记录跟随、但不经 `.tex` 白名单校验（Q10d，move 端点
  不限后缀）：改后缀后的主文件后续编译可能失败，由用户自行把握。
