# LaTeXcellence — Project Summary

> 项目概要文件。项目结构变更时（新增/删除模块），必须同步更新此文件。

---

## 目录结构

```
latexcellence/
├── main.py                          # FastAPI 入口 + 静态文件挂载
├── docker-compose.yml               # 多实例编排（v2 P7：app 副本扩展 + 可选 Caddy 反代 profile；override 样例同目录）
├── config.json                      # 配置文件
├── config.example.json              # 配置模板
├── requirements.txt                 # Python 依赖
├── pytest.ini                       # pytest 配置
├── .pre-commit-config.yaml          # pre-commit 钩子配置
├── AGENTS.md                        # AI 编程助手规范
├── DEVELOPMENT.md                   # 开发文档索引（指向 docs/ 目录）
├── DESIGN.md                        # 设计文档
├── PROJECT_SUMMARY.md               # ← 本文件：项目概要（供 AI 快速读取）
├── README.md                        # 项目简介
├── THIRD_PARTY_NOTICES.md           # 第三方许可
├── clean-cache.sh / clean-cache.ps1 # 缓存清理脚本（Bash / PowerShell）
├── venv-shell.sh                   # 打开一个已激活 .venv 的交互式 bash
├── backend/
│   ├── manage.py                    # 多用户模式 CLI 管理工具（python -m backend.manage）
│   ├── core/
│   │   ├── config.py                # Pydantic 配置模型 + 热重载
│   │   ├── llm_client.py            # AsyncOpenAI 封装（流式 + 重试 + 限流 + Function Calling）
│   │   ├── model_router.py          # 模型路由器（槽位 → 档案客户端池，热重载清池）
│   │   ├── security_middleware.py   # CORS 白名单 + 共享访问令牌中间件（多用户模式：会话 + CSRF + 请求级用户上下文全局注入）
│   │   ├── logger.py                # TimedRotatingFileHandler 日志
│   │   ├── workspace_manager.py     # 多工作区管理（workspace_id → LatexProject）
│   │   ├── project_manager.py       # LaTeX 项目管理（文件树/段落/撤销）
│   │   ├── tool_registry.py         # Function Calling 工具注册表
│   │   ├── prompts.py               # 中央提示词管理模块（集中存储所有 LLM 提示词）
│   │   ├── base_paragraph_check.py  # 逐段检查服务基类（typo/expression/code_review/terminology 共享）
│   │   ├── access_control.py        # 统一访问控制：项目权限矩阵 + workspace 绑定校验依赖
│   │   └── multiuser/               # 多用户模式基础设施包（db/userspace/auth/clientip/registration_policy/ip2region_xdb/dependencies/quota/upload/upload_rar/upload_sessions/maintenance/maintenance_lease/migrate/sandbox_leases/llm_governance/edit_log/mail/pending_interactions/operation_contexts）
│   ├── utils/
│   │   ├── json_utils.py            # LLM JSON 解析/校验/重试
│   │   ├── file_ops.py              # 原子写入/备份/MD5
│   │   ├── agent_file_tools.py      # Agent 私有文件工具共享 schema、调度与 line_range 读取
│   │   ├── input_resolver.py        # \input/\include 递归解析
│   │   ├── time_utils.py            # 时间工具：当前日期/时间/星期格式化
│   │   ├── path_guard.py            # 项目路径安全校验唯一入口 resolve_project_path()
│   │   ├── tex_compile.py           # 统一 TeX 编译执行（texfot+latexmk、引擎自动检测、PDF/A、可选 -synctex=1 供 SyncTeX 查询）
│   │   ├── sandbox/                 # Docker 沙箱执行包（v2 P1a：SandboxRunner 双后端、并发租约、ExecSpec/ExecResult 契约；部署收口阶段三：docker 后端容器预创建池 warm pool——形状匹配复用 + 每副本孤儿对账）
│   │   ├── session_mutex.py         # 跨进程会话互斥（v2 P7：会话目录旁 flock 锁文件 + 进程内 asyncio.Lock 叠加）
│   │   ├── latex_parser.py          # LaTeX 源码↔纯文本转换（Marker 保护）
│   │   ├── latex_json_guard.py      # LaTeX×JSON 转义防护（内容校验器，跨模块共享）
│   │   ├── latex_literals.py        # LaTeX 字面内容识别（逐字命令/URL 参数，parser 与 guard 共享）
│   │   ├── latex_package_rules.py   # LaTeX 宏包规则表（命令/环境/列说明符 → 宏包映射）
│   │   ├── latex_package_analyzer.py # 宏包检测：确定性规则表为主，LLM 兜底
│   │   ├── compile_error_translator.py  # LaTeX 编译错误 LLM 翻译（compile + git_diff 复用）
│   │   ├── latex_formatter.py       # 统一 LaTeX 格式化工具（code_review / formula_ocr / table_ocr 共用）
│   │   ├── latexindent_utils.py     # latexindent.pl 调用封装（latex_formatter 使用）
│   │   ├── latexindent.yaml         # latexindent 统一缩进配置（每层 4 个空格）
│   │   ├── builtin_formatter.py     # 内置轻量 LaTeX 格式化器（latex_formatter 兜底）
│   │   ├── web_search/               # 联网搜索模块（多引擎抽象包）
│   │   │   ├── __init__.py           # 模块入口，向后兼容导出
│   │   │   ├── models.py             # legacy 搜索响应 + v2 搜索候选/引擎状态契约
│   │   │   ├── planner.py            # 结构化查询计划契约 + 校验重试 + 规则回退
│   │   │   ├── ranking.py            # 多引擎候选合并、RRF 与域名多样性
│   │   │   ├── url_utils.py           # URL 安全规范化、跟踪参数清理与来源分类
│   │   │   ├── errors.py             # v2 类型化抓取失败与产物契约
│   │   │   ├── base.py               # BaseSearchEngine 抽象基类
│   │   │   ├── manager.py            # SearchManager + legacy/聚合策略 + 截止时间/熔断编排
│   │   │   ├── deep_read.py          # DeepReader — 搜索结果页面正文抓取
│   │   │   ├── page_reader.py         # v2 结构化读页、抽取缓存与证据编排
│   │   │   ├── query_rewriter.py     # QueryRewriter — LLM 搜索查询重写
│   │   │   ├── fetch/                # v2 抓取策略、条件请求与原始响应缓存
│   │   │   │   ├── browser.py       # 可选 Playwright 回退池、健康状态与资源隔离
│   │   │   │   ├── detector.py      # 页面阻断/挑战/付费墙/JS shell 分类
│   │   │   │   ├── scheduler.py     # 域名并发、请求间隔与熔断调度
│   │   │   │   ├── coordinator.py   # 抓取协调器
│   │   │   │   ├── cache.py         # 原始响应缓存
│   │   │   │   └── policy.py        # 抓取策略（条件请求）
│   │   │   ├── extract/              # v2 MIME 路由、多候选抽取、质量评分、切块与证据选择
│   │   │   └── engines/              # 具体搜索引擎实现
│   │   │       ├── ddg.py            # DuckDuckGo HTML 引擎 + DDG Challenge 处理
│   │   │       ├── searxng.py        # SearXNG 引擎
│   │   │       ├── tavily.py         # Tavily Search API 引擎
│   │   │       └── mock.py           # Mock 引擎（测试/降级）
│   │   ├── scholarly_research/        # 学术研究工具（多 Provider 联邦检索、详情、引文追踪、OA 定位）
│   │   │   ├── __init__.py            # 包入口与生命周期 API
│   │   │   ├── models.py              # 统一作品、作者、主题和结果模型
│   │   │   ├── planner.py             # v2 学术查询计划契约（尚未接入运行时）
│   │   │   ├── base.py                # 学术数据源抽象接口
│   │   │   ├── manager.py             # ResearchManager + Provider 编排 + Function Calling 工具
│   │   │   ├── _utils.py              # 内部共享工具（DOI/URL 归一化等）
│   │   │   └── providers/             # 可插拔学术数据源
│   │   ├── tool_result_protocol.py  # 工具结果统一协议（shown/total/truncated/next_step 状态块）
│   │   ├── project_tools/            # LLM Function Calling 工具集（浏览 + 编辑）
│   │   │   ├── __init__.py
│   │   │   ├── manager.py            # ProjectToolsManager — 浏览工具（list_files/read_file_content/get_section_structure/search_across_files）
│   │   │   └── edit_tools_manager.py # EditToolsManager — 段落编辑工具（edit_paragraph/undo_last_edit）
│   │   ├── spa_static_files.py        # SPA 回退静态托管（history 路由刷新/直达回退 index.html；main.py 生产模式挂载 static/ 使用）
│   │   └── search_router.py         # 通用搜索 API 路由（/api/search/ddg-challenge）
│   └── modules/                     # 功能模块目录
│       ├── auth/                    # 多用户认证与管理路由（/api/auth、/api/admin）
│       ├── projects/                # 多用户项目（上传/下载/回收站/配额/权限协作，/api/projects）
│       ├── typo_check/              # 错字查找
│       ├── expression_opt/          # 表达优化
│       ├── terminology_check/       # 术语一致性
│       ├── paragraph_translate/     # 逐段翻译
│       ├── code_review/              # LaTeX 代码审查
│       ├── compile/                 # 自动编译 + SyncTeX 正反向只读查询（synctex view/edit 经沙箱零写盘执行，响应只含相对路径）
│       ├── git_diff/                # Git 差异高亮
│       │   └── service/             # 服务包（models/constants/process/revisions/flatten/diff/render）
│       ├── git_manage/             # Git 版本管理（init/status/commit/log + 上传自动初始化）
│       ├── template_gen/            # 模板生成（自主 Agent）
│       │   └── agent/               # Agent 包（service/executor/validation/compile/sandbox/loop/constants）
│       ├── template_library/        # 模板库（内置模板 + 用户私有模板库，/api/templates，零 LLM）
│       │   └── builtin_templates/   # 内置模板包内资源（manifest + assets 元资源 + LaTeX 工程）
│       ├── formula_ocr/             # 公式 OCR（含 visual_inline.py 视觉 inline/display 判定）
│       ├── table_ocr/               # 表格 OCR
│       ├── format_converter/        # 格式转换
│       ├── style_modify/            # 样式修改（粘贴代码 + 项目 Agent）
│       ├── writing_chat/            # 写作助手聊天（cleanup.py 保留期清理）
│       │   └── service/             # 业务逻辑包（state/sessions/conversation/edits/editor_chat/compaction/_deps）
│       ├── bib_manager/             # 参考文献管理（格式规则层 + DOI 多数据源查询 + 重复检测 + 文献库管理）
│       │   └── providers/           # 可插拔 DOI 数据源
│       ├── plot_gen/                # 科研绘图（上传数据 -> 矢量图 + TikZ）
│       ├── editor/                   # 网页编辑器后端（整文件读写 + 统一撤销记账 + 引用符号扫描 + 跨文件大纲扫描 + 一键格式化纯转换）
│       │   └── service.py            # file-content / save-file 服务（扩展名白名单、MD5 乐观锁、损坏特征校验、配额回滚接线）+ latex-symbols 全项目符号扫描（\label / \cite / bib key，零 LLM）+ outline 全项目 .tex 跨文件大纲扫描（分节命令口径与前端 outlineScan 同源，项目级缓存 + 保存失效）+ format-latex 一键格式化（复用 latex_formatter，不落盘）；编辑器助手端点见 writing_chat 包 editor_chat.py
│       └── project_router.py        # 项目管理路由
│
├── frontend/
│   ├── package.json                 # npm 依赖与脚本
│   ├── vite.config.js               # Vite 配置（dev 代理目标随 VITE_API_BASE_URL 派生；vendor 分包含 pinyin-pro / pdfjs-dist 等按需懒加载块）
│   ├── .env.example                 # 环境变量模板（VITE_API_BASE_URL：维护者默认后端地址）
│   ├── index.html                   # 入口 HTML
│   ├── components.json              # shadcn/ui 组件配置
│   ├── jsconfig.json                # JS 路径别名配置
│   ├── public/                      # 静态资源（favicon）
│   ├── test/                        # 前端单元测试（node --test：formulaWrap / writingChatUtils / editorFiles / editorPreferences / editorTabs / editorSessionPersistence / editorChangeHistoryDiff / editorChangeHistory / editorStats / editorTabViews / editorMatcher / editorSnippetInsert / editorPreambleScan / editorSynctexMath / editorOutlineProject / editorLatexGrammar / latexCommands / i18nCoverage 等）
│   └── src/
│       ├── main.jsx                 # React 入口
│       ├── App.jsx                  # 根路由
│       ├── index.css                # 全局样式（Tailwind + 代码块 CSS 变量）
│       ├── i18n/                    # i18n 多语言配置 + 翻译资源
│       │   ├── index.js             # i18next 初始化（含语言检测）
│       │   ├── language.js          # 语言代码归一化 + 后端语言名映射
│       │   └── locales/             # 翻译 JSON 文件
│       ├── data/
│       │   └── latexCommands/         # LaTeX 命令数据库（阶段三，按类目拆分的 JSON + 统一加载器）
│       │       └── index.js             # schema 校验、LATEX_COMMANDS 导出、类目分组
│       ├── lib/                     # 通用前端工具
│       │   └── editor/                 # 编辑器纯模块
│       │   │       ├── language.js           # LaTeX 语言包抽象层（唯一入口，按文件后缀分流：.tex/.sty/.cls → 自研 Lezer 子集语法，.bib 及未知 → stex StreamLanguage，可整体替换）
│       │   │       ├── latexGrammar/         # 自研 MIT Lezer LaTeX 子集语法（阶段六a P0 高亮：latex.grammar 源 + \verb 外部分词器 + tags 映射 + LanguageSupport 组装 + 生成脚本；生成物不入库，npm pre 钩子先行生成）
│       │   │       ├── files.js              # 可编辑白名单 + 约 2MB 大小上限纯函数
│       │   │       ├── preferences.js        # 偏好默认值 + 白名单清洗 + 导出导入校验（kind/version/prefs；v2 含自定义片段条目级清洗）
│       │   │       ├── matcher.js            # 统一匹配器（中文/全拼/首字母/英文四路，拼音预计算缓存；行内补全与面板共用）+ 自定义片段派生索引
│       │   │       ├── customCommands.js     # 自定义片段消费侧合并层（归一化 / 与内建库合并 / 面板独立分组）
│       │   │       ├── commandZh.js          # 命令中文名按界面语言本地化展示（opencc-js 简转繁，动态加载）
│       │   │       ├── latexCompletion.js    # \ 与 \begin{ 行内补全 source（CM6 autocompletion）
│       │   │       ├── latexSymbols.js       # 引用补全符号缓存（项目为键 + 保存失效）与 \ref/\label/\cite 候选映射
│       │   │       ├── completionTheme.js    # 补全弹层主题（CM6 EditorView.theme + 高亮样式）
│       │   │       ├── snippetInsert.js      # 模板插入 + 占位符解析/网格骨架生成 + Tab 循环跳转模式
│       │   │       ├── wrap.js               # 包裹命令纯逻辑（多选区包裹计算 + Ctrl/Cmd+B/I/U/K 直包键位）
│       │   │       ├── beginClose.js         # \begin{env} 打字机自动补 \end（transactionFilter，autoCloseEnv 开关）
│       │   │       ├── changeHistoryDiff.js  # 行级 diff 纯模块（前后缀剪除 + Myers O(ND) + 防退化护栏）
│       │   │       ├── changeHistory.js      # 修改行色条 CM6 扩展（savedBuffer/openedBuffer 基线注入 + 橙/绿行装饰）
│       │   │       ├── stats.js              # 文档统计纯模块（中西文字数口径）+ 状态栏发布-订阅 hub
│       │   │       ├── synctexMath.js        # SyncTeX 坐标换算纯模块（点→CSS 像素、左上原点翻转、缩放限幅/步进/适宽）
│       │   │       ├── outlineScan.js        # 大纲扫描纯模块（分节命令解析 / 注释与 verbatim 排除 / 标题净化）
│       │   │       ├── outlineProject.js     # 跨文件大纲分组纯模块（后端 outline 响应按文件分组 + 激活文件置顶排序）
│       │   │       ├── formulaHoverScan.js   # KaTeX 悬停公式区域识别纯模块（$/\\(\\)/\\[\\]/$$/显示环境，注释与转义边界 + 光标快照→文档偏移换算）
│       │   │       ├── diagnostics.js        # 实时语法诊断纯模块（\begin/\end 栈匹配 + 花括号计数，注释/\verb/verbatim/转义/URL 字面量豁免，200 条截断）
│       │   │       ├── liveLint.js           # CM6 lint 扩展（linter delay 500ms，诊断消息 i18n 三语组装）
│       │   │       ├── formulaHover.js       # CM6 hoverTooltip 公式悬停预览（katex 动态分块 import + 主题化）
│       │   │       ├── searchPanel.js        # CM6 查找替换面板主题化 + 面板文案三语短语（EditorState.phrases）
│       │   │       ├── preambleScan.js       # 导言区扫描 + 宏包缺失检测 + usepackage 插入点选取
│       │   │       ├── logFileStack.js       # 编译日志文件栈解析（(./ 压栈 ) 弹栈 + l.N 归属栈顶，79 列 dewrap，歧义回落低置信）
│       │   │       ├── applyDiff.js          # unified diff 解析与上下文锚定应用（助手 diff 建议卡片）
│       │   │       ├── tabs.js               # 多缓冲区 tab 状态机纯归约（打开/激活/关闭/保存回写）与会话选择器
│       │   │       ├── sessionPersistence.js # 会话持久化纯函数（localStorage 主存 + sessionStorage 遗留收养迁移/序列化/白名单清洗/LRU 配额淘汰）
│       │   │       ├── tabViews.js           # per-tab CM6 EditorState / 滚动位置登记表（单实例换档）
│       │   │       └── assistantStream.js    # 编辑器助手 SSE 事件归约纯函数（占位消息/工具状态/工具确认卡/会话 id/DDG 挑战归约，供 hook 与单测共用）
│       ├── pages/                   # 页面组件
│       │   ├── LoginPage.jsx        # 多用户登录页（含忘记密码入口）
│       │   ├── RegisterPage.jsx     # 多用户注册页（含待审批提交态）
│       │   ├── ForgotPasswordPage.jsx / ResetPasswordPage.jsx # 自助找回密码（请求页 + 令牌重置页，v2 P6）
│       │   ├── PendingApprovalPage.jsx # 待管理员批准占位页
│       │   ├── ProjectWorkbenchPage.jsx # 项目工作台（列表/上传/配额/分类用量与清理/回收站/协作）
│       │   ├── EditorPage.jsx        # /editor 编辑器页（三栏布局 + 多缓冲区 tab 条 + 打开-编辑-保存-冲突闭环 + 刷新恢复）
│       │   ├── AdminPage.jsx        # 管理面板（审批/禁用/重置密码/配额/删除）
│       │   ├── TemplateCenterPage.jsx # 模板中心页（内置/我的模板浏览、上传、从模板创建项目）
│       │   ├── code-review/         # 代码审查子组件
│       │   └── writing-chat/        # 写作聊天子组件
│       ├── hooks/                   # 自定义 hook
│       │   ├── useParagraphCheck.js # 逐段检查统一流程 hook（含检查结果保鲜 MD5 比对）
│       │   ├── useWritingChat.js    # 写作聊天生命周期 hook
│       │   ├── useEditorAssistant.js # 编辑器 AI 助手 SSE hook（事件归约 + 项目切换守卫 + 工具确认/取消动作（复用 confirm-tool/cancel-tool 端点）+ 复用 useWritingChatCaptcha 挑战通道）
│       │   ├── usePlotGen.js        # 科研绘图 Agent hook
│       │   ├── useStyleModifyAgent.js # 样式修改项目 Agent hook
│       │   ├── useAuth.js           # 多用户认证引导（模式探测 → /api/auth/me 恢复会话）
│       │   ├── useCanWrite.js       # 多用户写权限 hook（owner/editor 白名单，单一事实源）
│       │   ├── useIsOpened.js       # 模式感知"项目已打开"hook（单用户看 projectPath，多用户看 projectId）
│       │   ├── useRegistrationEnabled.js # 多用户注册开放状态 hook（registration_enabled=false 时隐藏注册入口）
│       │   ├── useEnabledFeatures.js # 功能开关加载 hook（幂等）
│       │   ├── useUndoRedo.js        # 撤销/重做状态 hook
│       │   ├── useResultFilter.js    # 检查结果排序过滤 hook
│       │   ├── useTheme.js           # 亮/暗主题切换 hook
│       │   ├── useFavicon.js         # 页面图标 hook
│       │   ├── writing-chat/        # 写作聊天子 hooks（sessions/assistants/captcha/sse/utils）
│       │   └── bib-manager/         # 参考文献管理子 hooks（format_analysis/metadata_extraction/bibtex_generation/bib_file_save/latex_generation）
│       ├── components/              # 共享组件 + ui 基座组件
│       │       ├── AuthShell.jsx        # 多用户认证页公共外壳
│       │       ├── CaptchaDialog.jsx    # DDG 搜索验证码弹窗（Writing Chat 页与编辑器助手侧栏共用）
│       ├── AuthConnectionButton.jsx # 认证页工具栏「后端连接设置」入口（改绑后端）
│       │   ├── editor/                 # 编辑器组件
│       │   │   ├── LaTeXEditor.jsx     # CodeMirror 6 受控封装（value/onChange + 多缓冲区 per-file EditorState 换档，挂行内补全与占位符扩展）
│       │   │   ├── EditorTabBar.jsx    # 多缓冲区标签条（脏标记圆点 + 磁盘已变更标记 + 关闭按钮 + 全部保存）
│       │   │   ├── EditorToolbar.jsx   # 后端撤销重做 · 命令面板 · 编译 · 保存 · 设置
│       │   │   ├── EditorStatusBar.jsx # 编辑器底部状态栏（光标行列 / 选区 / 行数 / 中西文字数，统计 hub 订阅）
│       │   │   ├── OutlinePanel.jsx    # 左栏大纲面板（当前文件 / 本项目双视图：激活缓冲区四级大纲 + 后端跨文件分组置顶，点击条目开 tab 行定位）
│       │   │   ├── FormulaPreviewCard.jsx # 当前公式固定预览卡（编辑区底部悬浮，光标通道防抖跟随，无公式/渲染失败静默隐藏）
│       │   │   ├── EditorSettingsDialog.jsx # 偏好开关（软换行/字号/自动保存/环境自动配对/实时语法诊断）+ 自定义片段管理（增删改 + 实时预览）+ 偏好导入导出双通道
│       │   │   ├── EditorCloseTabDialog.jsx # 关闭脏 tab 三选弹窗（保存并关闭/放弃改动/取消，autoSave 开启默认前者）
│       │   │   ├── ConflictDialog.jsx  # 保存 409 冲突弹窗（按冲突归属 tab 出路：放弃重载 / 剪贴板逃生 / 覆盖保存）
│       │   │   ├── CommandPalette.jsx  # 命令面板（Ctrl/Cmd+/ 全量；Ctrl+L 包裹命令专项浮层；搜索 + 分类 + 网格选择器二级步骤）
│       │   │   ├── GridSizePicker.jsx  # 表格/矩阵 m×n 拖选网格选择器
│       │   │   ├── AssistantPanel.jsx  # AI 助手侧栏（SSE 对话 + 三种建议应用形态卡片 + DDG 挑战弹窗）
│       │   │   ├── PdfJsPreview.jsx    # pdf.js 渲染的 PDF 预览（iframe 替代：懒加载分块 + 最小工具条 + 正向高亮浮层 + 反向双击取点）
│       │   │   ├── FullscreenPDFPreview.jsx # PDF 全屏预览覆盖层（页面级 fixed，正向/反向通道透传）
│       │   │   └── StaleCheckBanner.jsx # 检查结果过期横幅（各逐段检查页复用）
│       │   ├── UploadProjectDialog.jsx # 上传项目对话框（zip/7z/rar 或目录分片直传 + 断点续传 + 暂停/进度）
│       │   ├── ProjectPermissionsDialog.jsx # 项目权限对话框（协作者管理/转交）
│       │   ├── template-center/     # 模板中心组件（卡片/详情/上传/编辑/创建项目/另存为/生成入库对话框与缩略图）
│       │   └── settings/            # 设置对话框分区配置组件
│       ├── theme/                   # 主题 CSS + useThemeManager.js
│       ├── fonts/                   # 字体加载（dev 本地 npm 包 / build 走 cdnjs）
│       ├── api/index.js             # 统一 API 层（接口分组封装）
│       ├── api/connection.js        # 后端连接配置（基地址/令牌/认证头/连接测试）
│       ├── utils/                   # 前端工具（errorMessages.js 错误翻译（精确映射）/ errorPatterns.js 错误模式匹配纯模块（PATTERN_MAP + matchPattern）/ format.js 字节与时间展示格式化 / formulaWrap.js 公式包裹重写 / highlight.js 高亮入口 / md5.js 增量 MD5（分片上传校验）/ uploadQueue.js 分片上传队列纯状态机（建队/位图对位/退避重试/resume 复位）/ multiUserProject.js 多用户项目打开统一入口 / projectAccess.js 双模式打开与写角色白名单纯函数）
│       ├── styles/                  # 前端样式（code-highlight.css：高亮颜色独立文件，按需引入）
│       ├── store/useStore.js        # Zustand 全局状态
│       └── store/editorStore.js     # 编辑器状态（editorPreferences persist 切片 + editorSession 多缓冲区切片（tabs/激活位/冲突） + editorAssistant 助手会话切片（含工具确认卡状态））
│
├── showcase/                        # 独立展示页（零依赖静态站，详见 docs/showcase.md）
├── screenshots/                     # 项目截图（README / 文档配图）
├── docker/                          # Docker 沙箱镜像体系（v2 P1a：backend/tex/convert/plot 四镜像 + build.sh + README）
├── docs/                            # 开发文档
│   ├── development.md               # 项目概述/技术栈/启动
│   ├── backend-core.md              # 后端核心模块
│   ├── modules.md                   # 模块索引
│   ├── frontend.md                  # 前端架构
│   ├── testing.md                   # 测试规范
│   ├── latex-package-rules.md       # 宏包规则表维护指南
│   ├── duckduckgo-challenge.md      # DDG 验证码处理说明
│   ├── function-calling-tools.md    # Function Calling 工具体系说明
│   ├── templategen-optimization-lessons.md  # template_gen 闭环优化复盘与通用性门禁
│   ├── plotgen-optimization-lessons.md      # plot_gen 闭环优化复盘与最佳实践
│   ├── prompt-guidelines.md                 # 提示词工程原则与约定（统一结构/回归口径）
│   ├── windows-temp-permissions.md          # Windows 沙箱临时目录权限排查（pytest basetemp/ACL/清理）
│   ├── web-access-operations.md              # 网络访问灰度、观测、容量与回滚运维
│   ├── multi-user.md                           # 多用户模式设计（双模式/认证/用户空间/配额/权限协作/沙箱/撤销/邮件/多实例；v1+v2 合并的权威设计参考）
│   ├── rar-extractor-fallback.md                 # RAR 解压器回退层设计（unar → bsdtar，已实施）
│   ├── editor-design.md                            # 网页编辑器 v1 设计（CM6 编辑器 + 命令快速输入 + AI 助手；已实施，设计与决策记录）
│   ├── editor-productivity-design.md                # 编辑器生产力改造 v2 设计（多缓冲区 tab/大纲/引用补全/格式化/KaTeX/错误跳转/修改行色条；已实施，设计与决策记录）
│   ├── editor-experience-design.md                # 编辑器写作体验收口设计方案（编辑器 v3：SyncTeX 正反向同步/用户自定义片段/实时语法诊断/跨文件大纲与 KaTeX 预览卡/助手接入联网与学术检索/自研 MIT Lezer 子集语法；阶段一至五及六a 已实施，六b 待实施）
│   ├── multi-model.md                       # 多模型支持设计（模型档案 + 角色槽位路由；已实施）
│   ├── deployment.md                          # 后端部署指南（单用户/多用户单进程/多实例 Compose 部署、systemd 托管、HTTPS 反向代理、安全风险声明、备份、环境变量与升级回滚）
│   ├── deploy-followups.md               # 多用户部署侧收口设计（rar 解压入沙箱/真实 RAR 样本/warm pool/模型连通性测试；已实施）
│   ├── modules/                     # 模块文档（含 editor.md 网页编辑器）
│   └── showcase.md                  # 展示页（showcase）说明
├── tests/                           # 测试用例
│   ├── conftest.py                  # pytest 共享 fixture
│   ├── test_api/                    # 各模块 API 集成测试
│   ├── test_multiuser/              # 多用户模式测试（认证/项目/配额/协作/DB 迁移/沙箱租约/LLM 治理/rar 上传/衍生数据记账与清理/分片上传与断点续传/邮件队列与自助找回）
│   ├── test_plot_gen/               # plot_gen 单元测试
│   ├── test_template_library/       # 模板库测试（模型/内置发现/用户库/API/多用户隔离与配额）
│   ├── test_docker/                 # Docker 沙箱真容器冒烟 + compose 编排冒烟（marker docker，无 daemon 自动 skip）
│   ├── test_utils/                  # 后端工具单元测试（路径/JSON/宏包/搜索/学术检索/编译/sandbox 契约等）
│   └── fixtures/                    # 测试数据（宏包规则表、公式/表格 OCR、错字查找/表达优化语料等）
├── data/                            # 应用运行期与用户数据统一目录
│   ├── latexcellence.db             # 多用户模式 SQLite 数据库（运行时生成）
│   ├── users/                       # 多用户模式用户空间（<user_id>/ 下 projects 与衍生数据目录）
│   ├── staging/                     # 多用户模式上传解压暂存区（运行时生成）
│   ├── formats/                     # 参考文献格式定义
│   ├── plots/                       # plot_gen 绘图运行输出（运行时生成）
│   ├── prompts/                     # 自定义提示词（expression/ 风格预设与 writing_chat/ 角色预设）
│   ├── style_modify_sessions/       # style_modify Agent 会话缓存
│   ├── templates/                   # 生成的 LaTeX 模板（<session_id>/ 沙箱产物）
│   ├── template_library/            # 单用户模式模板库（多用户按用户空间隔离到 users/<user_id>/template_library/）
│   ├── writing_chat_sessions/       # 写作助手聊天持久化数据
│   └── web_cache/                   # 结构化网页原始/抽取缓存（运行时生成，可清理）
├── logs/                            # 运行时日志
├── tools/                         # 维护者工具（开发/评测/维护用脚本与 Agent 评测工作区，不进入运行时）
│   ├── eval_common.py             # 人工评估工具共享基座（语料加载/工作区/marker 还原/命中判定/CLI 骨架）
│   ├── web_access_eval/            # Writing Chat 网络访问评测（离线重放、legacy 手动采集、阶段 6 容量/回滚检查）
│   ├── visual_review_api.py       # 视觉评审工具的 Responses API 请求适配
│   ├── generality_gate.py         # Agent 闭环优化通用性门禁（硬编码特征扫描/提示词体积监控/holdout 解析/对照组回归判定）
│   ├── tex_path.py                # 评测工具共享：TeX Live 路径自动发现（非交互 shell 也能定位 latexmk）
│   ├── builtin_templates/         # 内置模板验证脚本（verify_templates.py：编译验证 + --gen-assets 元资源生成，维护者专用）
│   ├── latex_packages/            # 宏包规则表维护脚本（编译验证 + 覆盖率报告）
│   │   ├── verify_rules.py        # 离线编译验证（维护者专用，需 TeX）
│   │   ├── coverage_report.py     # 覆盖率报告（对照常用宏包清单）
│   │   └── common_packages.txt    # 常用宏包期望清单（覆盖率基准）
│   ├── formula_ocr_eval/          # 公式 OCR 人工评估工具（渲染语料 → 跑 OCR → 生成比对报告）
│   ├── table_ocr_eval/            # 表格 OCR 人工评估工具（渲染语料 → 跑 OCR → 生成比对报告）
│   ├── typo_check_eval/           # 错字查找人工评估工具（加载语料 → 跑检查 → 生成比对报告）
│   ├── expression_opt_eval/       # 表达优化人工评估工具（加载语料 → 跑优化 → 生成比对报告）
│   ├── terminology_check_eval/    # 术语一致性人工评估工具（加载多段术语语料 → 跑检查 → 生成比对报告）
│   ├── paragraph_translate_eval/  # 逐段翻译人工评估工具（加载语料 → 跑翻译 → 生成比对报告）
│   ├── prompt_comprehension_eval/ # Prompt 理解评测工具（逐一探测 + 历史追踪 + 回归检测）
│   ├── rar_fixtures/               # rar 测试样例工具（make_fixtures.py 纯字节构造 store 模式 RAR4 入库；verify_fixtures.py 对全部入库样本做结构/预扫/真实解压对账；真实压缩样本由 rar CLI 生成，步骤见前者 docstring）
│   ├── warm_pool_bench.py          # 沙箱容器预创建池冷启动基准（同 ExecSpec 冷启 N 次对比池路径 N 次，输出准备耗时/墙钟 P50/P95，需 Docker daemon）
│   ├── compose-smoke.sh           # 多实例 compose 冒烟（replicas=2：注册审批→分片上传→undo→下载；--with-tex 追加编译冒烟）
│   ├── bump-version.sh            # 版本号一键升级（main.py 两处 + 前端三语 i18n；major/minor/patch 或显式版本号，默认自动提交）
│   ├── plotgen_opt/               # plot_gen Agent 优化评测工作区（脚本/数据集/报告入库，results、previews 忽略）
│   ├── templategen_opt/           # template_gen Agent 优化评测工作区（脚本/场景定义/参考资料入库，results 忽略）
│   └── stylemodify_opt/           # style_modify Agent 优化评测工作区（脚本/场景定义/报告入库，results、runtime 忽略）
└── static/                        # Vite 构建产物
```

## Feature Modules Quick Reference

| Module | 目的 | 关键文件 | 依赖 LLM | Marker |
| --- | --- | --- | --- | --- |
| auth | 多用户认证与管理（注册/审批/登录/会话/管理员路由） | router.py, service.py, core/multiuser/* | ✗ | ✗ |
| projects | 多用户项目：整包上传/分片上传（断点续传）、安全解压、命名去重、配额记账、回收站、下载、协作者管理与转交（/api/projects） | modules/projects/*, core/multiuser/quota.py, upload.py, maintenance.py | ✗ | ✗ |
| typo_check | 错字/拼写检查 | router.py, service.py | ✓ | ✓ |
| expression_opt | 学术表达优化 | router.py, service.py, prompt_manager.py | ✓ | ✓ |
| code_review | LaTeX 代码审查（工具格式化 + LLM 质量审查） | router.py, service.py | ✓ | ✗ |
| terminology_check | 术语一致性检查 | router.py, service.py, extractor.py, models.py | ✓ | ✓ |
| paragraph_translate | 逐段翻译 | router.py, service.py | ✓ | ✓ |
| compile | 编译 + 错误翻译 + 引擎自动检测 + PDF/A 支持 + SyncTeX 正反向只读查询（synctex view/edit 经沙箱零写盘，响应只含相对路径） | router.py, service.py | ✓(错误翻译) | ✗ |
| git_diff | Git 版本差异 PDF | router.py, service/ | ✓(编译失败时翻译错误) | ✗ |
| git_manage | Git 版本管理最小闭环：自动/手动初始化、变更列表、一键提交、历史（与 git_diff 共享执行层）；.git 配额豁免 + 独立上限 | modules/git_manage/*, core/multiuser/quota.py, modules/git_diff/service/ | ✗ | ✗ |
| template_gen | 模板生成（自主 Agent） | router.py, service.py, agent/, trace.py, cleanup.py | ✓ | ✗ |
| template_library | 模板库：内置模板发现 + 用户私有模板库（上传 zip/项目另存为/生成结果入库）、从模板创建项目（清洗复制，多用户配额预检超限 413）、内置模板缩略图与 PDF 预览；零 LLM | router.py, service.py, models.py, builtin_store.py, user_store.py, builtin_templates/, core/multiuser/quota.py | ✗ | ✗ |
| formula_ocr | 图片→LaTeX 公式 | router.py, service.py, visual_inline.py | ✓(Vision) | ✗ |
| table_ocr | 图片→LaTeX 表格 | router.py, service.py | ✓(Vision) | ✗ |
| format_converter | 格式转换(pandoc) | router.py, service.py | ✓(宏包分析仅兜底) | ✗ |
| style_modify | 粘贴代码 / 项目 Agent 修改样式，支持 shadow 编译、proposal 审核、批量事务与撤销 | router.py, service.py, agent.py, models.py, session.py, trace.py, cleanup.py | ✓ | ✗ |
| writing_chat | 写作助手对话（含联网搜索、学术检索与引文追踪、项目浏览、段落编辑和工具确认） | router.py, service/, context.py, models.py, prompt_manager.py | ✓ | ✗ |
| bib_manager | 参考文献管理 + 格式规则层 + DOI 多数据源查询 + 重复检测 + 文献库 | router.py, service.py, models.py, bib_parser.py, format_rules.py, style_registry.py, provider_manager.py, providers/ | ✓(格式规则命中不依赖 LLM；DOI 查询不依赖 LLM) | ✗ |
| editor | 网页编辑器：后端整文件打开/保存（MD5 乐观锁、统一撤销记账与配额回滚接线）+ 前端 /editor 页 CM6 打开-编辑-保存-冲突闭环 + 命令快速输入子系统（命令数据库四路匹配查找、\ 行内补全与命令面板双入口、表格矩阵网格选择器、宏包缺失联动自动添加声明）+ 多缓冲区基座（tab 条、per-tab 缓冲区/撤销/滚动、陈旧检测、关闭三选、sessionStorage 刷新恢复、per-tab 自动保存、编译全量脏保存、助手 tab 守卫） + 编辑基本功包（包裹命令快捷键 Ctrl/Cmd+B/I/U/K 直包与 Ctrl+L 包裹浮层、wrap 类目进命令数据库与命令面板、多光标、\begin{env} 打字机自动补 \end（autoCloseEnv 开关）、查找替换面板主题化与三语） + 编译联动（脏改动先自动保存）+ 引用补全（\ref/\label/\cite 三触发 source + 后端 latex-symbols 全项目符号扫描 + 项目级缓存保存失效）+ 一键格式化（工具栏按钮与 Ctrl/Cmd+Shift+F 快捷键 + 后端 format-latex 纯转换端点（latexindent 优先、内置兜底），结果直接替换发起 tab 缓冲区并置脏（单事务整文档替换，Ctrl+Z 一步撤销），落盘仍走 save-file 单一路径，viewer 禁用）+ 编译错误列表与跨文件跳转（右栏可点击错误列表 + 点击联动打开/激活 tab 并行首定位高亮；logFileStack 日志文件栈解析纯模块（`(./` 压栈、`)` 弹栈、`l.N` 归属栈顶、79 列 dewrap），归属歧义回落发起编译的文件并标低置信）+ 编辑器侧 AI 助手（复用 Writing Chat 会话基础设施的缓冲区协议端点、自适应上下文注入、插入光标/替换选区/diff 卡片三种建议应用形态）+ 检查结果保鲜横幅 + 偏好导入导出 + 修改行色条（阶段八借鉴 Notepad++ Change History：savedBuffer/openedBuffer 双基线 + changeHistoryDiff 行级 Myers diff 纯模块（防退化护栏）+ changeHistory CM6 行装饰扩展，琥珀=未保存修改/翠绿=本会话已保存修改，Ctrl+Z 到保存点精确清零；dirty 改按 buffer≠savedBuffer 内容比对）+ 底部状态栏（stats 中西文字数统计纯模块 + createStatsHub 发布订阅 + EditorStatusBar，不经过页面 React 树）+ selectionMatches 选中高亮全部匹配偏好 + 会话持久化升级 localStorage（v2 载荷脏 tab 携带 savedBuffer，sessionStorage 遗留收养迁移，重启浏览器可恢复脏缓冲区）+ SyncTeX 正反向同步（editor v3 阶段一：编译链路可选 `-synctex=1` + synctex forward/backward 只读查询端点 + PDF 预览 iframe 替换为 pdf.js 渲染（懒加载分块、最小缩放/适宽/页码工具条、正向高亮浮层、双击反向取点，`PdfJsPreview` 对外 `{url}` 兼容使全屏预览等使用点自动受益），正向由 stats hub 光标通道防抖驱动（预览可见且有产物才查询）、反向复用错误跳转的开 tab + 行定位通路）+ 用户自定义片段库（editor v3 阶段二：偏好 persist v2 增 customSnippets（条目级清洗：命令名形状校验、同名去重、上限与单条 insert 长度上限，v1 导入载荷兼容不丢数据）+ customCommands 消费侧合并层（归一化补齐 pkg/grid 派生字段，%COLS%/%CELLS% 网格标记天然生效；条目机制层 expandEntryInsert/insertSnippet/$n 占位符零改动）统一并入行内补全、命令面板/包裹浮层与插入入口，面板独立 custom 分组三语 + 设置弹层自定义片段管理分区（列表增删改 + 占位符解析实时预览，跟随既有偏好导入导出双通道））+ 实时语法诊断（editor v3 阶段三：diagnostics 诊断纯模块（\begin/\end 栈匹配报未闭合/错嵌/多出的 \end，花括号全文档计数报多余 } 与未闭合 {，豁免注释/\verb 参数/verbatim 类环境/转义 \{\}/\url 系 URL 字面量，200 条上限超出截断并在首条标注）+ @codemirror/lint 接线（linter delay 500ms 编辑停顿后运行，内联波浪线 + 悬停提示，不进错误列表不阻断编译）+ liveDiagnostics 偏好开关（默认开，白名单缺省补齐不升版本，设置弹层三语开关，存档 tab 失配重建即时生效））+ 跨文件大纲与公式固定预览卡（editor v3 阶段四：后端 GET /api/project/outline 全项目 .tex 分节扫描（分节命令口径与前端 outlineScan 同源、asyncio.to_thread 调度、项目级缓存 + 保存失效、响应只含相对路径）+ 大纲面板当前文件/本项目双视图（本项目视图按文件平铺分组、激活文件置顶展开、其余组折叠可切换，纯模块 outlineProject 分组排序，点击条目复用错误跳转的 pendingErrorJumpRef 换档机制开 tab 行定位）+ 公式固定预览卡 FormulaPreviewCard（编辑区底部状态栏上方悬浮，订阅 stats hub 光标通道防抖约 400ms，findFormulaAt 识别 + loadKatex 单例渲染，无公式/渲染失败静默隐藏））+ 助手接入联网与学术检索（editor v3 阶段五a：`_EDITOR_TOOL_NAMES` 扩入 9 个联网/学术工具并与 Writing Chat 主对话注册表对齐（直通执行、写入仍走缓冲区协议）+ `_get_editor_tools` 三源汇聚按源独立容错（联网源受 web_search.enabled AND writing_chat.web_search_enabled 双开关、学术源受 scholarly_research.enabled AND providers.openalex.enabled 双开关，把关精确挂 schema 收集层并在工具循环执行前按同口径复核）+ 工具循环执行前白名单硬校验（幻觉/越权工具名不执行、注入失败 tool result，堵住幻觉调用 edit_paragraph 直达写盘的既有缺陷）+ DDGChallengeError 显式分支（captcha_challenge 事件 + done(captcha_pending) 终流，本轮工具消息不持久化）+ AssistantPanel 复用 useWritingChatCaptcha 通道与共享 CaptchaDialog（自 pages/writing-chat/ 提升）在侧栏内完成挑战 + assistantStream 归约扩展（captcha_pending 删占位不留幽灵消息、captcha_challenge 清工具指示器、挑战对象归一化）+ prompts 工具使用约定（何时检索/引用只来自工具真实来源/引用不进建议块）+ 工具确认流（editor v3 阶段五b：联网/学术九工具逐次确认——state.py 拆分登记/等待两阶段（_register_tool_confirmation/_await_tool_confirmation，_request_tool_confirmation 组合两者保持 Writing Chat 原行为），工具循环先登记 pending 再 yield tool_pending（修复既有 yield 先于登记的亚秒确认 404 时序缺陷）+ 流内等待复用 Writing Chat confirm-tool/cancel-tool 既有端点与 pending_interactions 基础设施（多用户表驱动跨副本先到先得、单用户 Event 路径不变，TTL 120s 过期等同取消）+ 拒绝/过期注入取消语义 tool result（模型明确知晓拒绝并可继续回答或换策略）+ assistantStream 归约扩展（reduceAssistantPendingTool 确认卡归约、tool_pending/tool_cancelled 清工具指示器、终流/断流清卡）+ AssistantPanel ToolConfirmCard 确认卡（工具名/查询摘要三语、TTL 过期灰色化视觉态、viewer 可确认无写权限守卫））+ 自研 Lezer 子集语法 P0 高亮（editor v3 阶段六a：language.js 唯一入口按激活 tab 后缀分流（.tex/.sty/.cls → 自研 MIT latexGrammar Lezer LanguageSupport，.bib 沿用 stex StreamLanguage）+ latexGrammar 包（latex.grammar 源覆盖行注释/命令含星号变体与参数/四种数学定界/环境/分组/上下标/分节命令 heading/verbatim 类环境与 \verb 任意定界符参数原样吞掉，\verb 由外部分词器以 fallback 语义实现；不追求 catcode 级正确与 $ 配对零误判，依赖 Lezer 错误恢复兜底）+ @lezer/generator devDependency 经 npm predev/prebuild/pretest 钩子生成 parser（生成物不入库，clean checkout 构建可重复）+ tags 与 stex 视觉逐类对齐（回归对照清单进测试头注，2MB 全量解析约 0.6s 数据点，折叠与环境缩进属六b）） | backend modules/editor/service.py（路由挂载于 project_router）、writing_chat/service/editor_chat.py；frontend pages/EditorPage.jsx、components/editor/、components/CaptchaDialog.jsx、lib/editor/、lib/editor/latexGrammar/、data/latexCommands/、store/editorStore.js | ✓(仅助手端点) | ✗ |
| plot_gen | 科研绘图 Agent（数据画像 -> 图表推荐 -> Python 执行 -> TikZ 编译） | router.py, service.py, agent.py, models.py, data_parser.py, recommender.py, styles.py, executor.py, tikzgen.py, sandbox_guard.py, trace.py, cleanup.py | ✓ | ✗ |

## 核心设计思路

> 本节只列关键概念的一句话说明与文档入口；完整设计见 [DESIGN.md](DESIGN.md)，开发细节见 [docs/](docs/)。

| 设计要点 | 一句话说明 | 详细文档 |
| --- | --- | --- |
| Marker 保护 | LaTeX 结构替换为 `__CMD_NNNN__` / `__MATH_NNNN__` 等 marker，LLM 只能修改纯文本；还原按创建顺序（counter）降序，非标准 marker 按长度降序；code_review 例外（建议只读展示，无 marker） | [DESIGN.md §2](DESIGN.md)、[backend-core.md](docs/backend-core.md) |
| JSON 输出校验流水线 | `safe_parse_json()` 容错解析 → Pydantic `model_validate()` 严格校验 → 失败反馈 LLM 重试（最多 3 次） | [DESIGN.md §3](DESIGN.md) |
| LaTeX×JSON 转义防护 | `latex_json_guard.py` 拦截控制字符、`[object Object]` 占位符、花括号 / `$` / 环境配平；分隔符协议等非 JSON 路径用 `relaxed=True` | [backend-core.md](docs/backend-core.md) |
| 分隔符协议 | OCR、style_modify、writing_chat 编辑输出用 `<<<LATEX>>>...<<<END>>>` 直接传输纯文本，从根上避免 JSON 转义损坏 | [formula_ocr.md](docs/modules/formula_ocr.md) 等模块文档 |
| 集中式提示词管理 | 所有 LLM 提示词集中存放在 `backend/core/prompts.py`，`get_prompt()` 参数化获取，`{language}` 占位符注入输出语言 | [backend-core.md](docs/backend-core.md) |
| 多模型支持（角色槽位路由） | `llm.profiles` 模型档案 + `assignments` 槽位指派，`get_client_for(role)` 按两级解析路由客户端（池化 + 热重载清池）；能力硬校验（OCR 槽必需 vision）、旧配置自动迁移、推理模型封装层兼容；设置页「模型管理」分区编辑（档案卡片「测试连接」发起 `POST /api/llm/test` 连通性自检，返回延迟/失败原因），功能入口模型徽标展示当前生效模型 | [backend-core.md](docs/backend-core.md)、[multi-model.md](docs/multi-model.md) |
| BaseParagraphCheckService | 多个逐段检查模块共享的基类，封装文件解析、marker 替换、LLM 编排与写回，子类通过 hooks 定制 | [backend-core.md](docs/backend-core.md) |
| CheckOperationContext | 每次 preview/check 生成唯一 `operation_id`，保存 marker 映射与 MD5 快照（TTL 1 小时），apply 时精确还原，防止 marker 污染源文件 | [DESIGN.md §1.4](DESIGN.md) |
| 乐观锁 | 读取时返回文件 MD5，写回时校验；不一致抛 `FileConflictError`（HTTP 409），前端刷新 MD5 后重试 | [DESIGN.md §4.2](DESIGN.md) |
| SSE 流式 | 段落检查、Agent 运行、写作助手均通过 SSE 推送 `progress` / `result` / `error` / `done` 等事件 | [modules.md](docs/modules.md) |
| Function Calling 工具体系 | `ToolRegistry` 统一注册与管理工具开关；SearchManager / ProjectToolsManager / EditToolsManager / ResearchManager 自注册；template_gen / plot_gen / style_modify 使用 session 私有工具 | [function-calling-tools.md](docs/function-calling-tools.md) |
| 工具结果统一协议 | list_files / read_file_content / search_across_files 与学术检索工具在结果末尾追加 `shown` / `total` / `truncated` / `next_step` 状态块，指导模型在结果被截断时缩小范围、继续读取或换用工具 | [backend-core.md](docs/backend-core.md) |
| 通用搜索聚合 | `SearchQueryPlan` 经统一 JSON 校验重试生成；`SearchManager` 可并发查询多个引擎，按 URL/近重复合并后使用 RRF、来源加权和域名上限排序，并保留 legacy 回滚路径 | [backend-core.md](docs/backend-core.md) |
| 宏包规则表检测 | 确定性规则表（命令/环境/列说明符 → 宏包）优先，零 LLM 开销；规则表未命中才 LLM 兜底 | [latex-package-rules.md](docs/latex-package-rules.md) |
| 多文件 \input 展开 | 递归解析 `\input` / `\include` / `\subfile`，段落索引为文件内局部索引，结果按文件分组展示与写回 | [backend-core.md](docs/backend-core.md) |
| 编译与错误翻译 | `tex_compile.py` 统一编译执行（texfot + 引擎自动检测 + PDF/A），`compile_error_translator.py` 把失败日志翻译为修复建议 | [compile.md](docs/modules/compile.md) |
| Docker 沙箱执行 | 全部外部命令经 SandboxRunner（docker/local 双后端）：容器无网络、只读根 fs + tmpfs、资源限额与并发租约；auto 探测（daemon ping + bind-mount 挂载预检，防容器化后端部署静默失效）失败回落本地零行为变化；镜像名留空按内置默认回落 | [backend-core.md](docs/backend-core.md)、[deployment.md](docs/deployment.md) |
| DOI 多数据源查询 | Crossref 主索引 + arXiv 专用路由 + 补全层，`ProviderManager` 全局单例管理可插拔数据源 | [bib_manager.md](docs/modules/bib_manager.md) |
| 自主 Agent | template_gen / plot_gen / style_modify 项目 Agent 基于 Function Calling 自主规划、执行、验证、自修，`trace.jsonl` 全程回放；私有文件工具复用 `agent_file_tools.py` 的 schema、调度与范围读取契约 | [function-calling-tools.md](docs/function-calling-tools.md)、[template_gen.md](docs/modules/template_gen.md) 等模块文档 |
| 代码展示与高亮 | CodeBlock / MarkerText / CodeEditorInput 统一 LaTeX 源码展示，token 颜色由 `--code-token-*` 变量控制 | [frontend.md](docs/frontend.md) |
| 逐段检查前端流程 | `useParagraphCheck` 统一封装恢复、预览、检查、排序、应用与持久化；`useResultFilter` 负责排序过滤 | [frontend.md](docs/frontend.md) |
| 写作助手 | `useWritingChat` 管理会话、SSE 流式、工具确认、编辑建议双轮协议 | [writing_chat.md](docs/modules/writing_chat.md) |
| 网页编辑器与编辑器侧 AI 助手 | `/editor` 页 CM6 缓冲区编辑经 save-file 单一路径落盘；助手复用 Writing Chat 会话基础设施，建议只改缓冲区（插入光标 / 替换选区 / unified diff 卡片），`editor.enabled` / `assistant_enabled` 双开关服务端把关；助手工具白名单（项目只读 + 联网搜索 + 学术检索，执行前硬校验），联网/学术工具经确认卡逐次确认后执行（拒绝/TTL 过期注入取消语义，先登记后下发 pending 事件），DDG 挑战在侧栏内完成（阶段五已实施） | [editor.md](docs/modules/editor.md)、[editor-design.md](docs/editor-design.md) |
| 检查结果保鲜 | 逐段检查结果记录检查时文件 MD5，页面加载时比对当前 MD5，不一致渲染 StaleCheckBanner 提示重查而不销毁结果 | [frontend.md](docs/frontend.md)、[editor.md](docs/modules/editor.md) |
| 主题系统 | 主题 CSS 自动发现，亮/暗模式独立；`--primary` / `--accent` 等由单一色相派生 | [frontend.md](docs/frontend.md) |
| 多用户双模式 | `multi_user.enabled` 开关；单用户保持现状（本地目录 + 共享令牌），多用户强制会话认证、CSRF、禁用任意路径打开/浏览；服务器路径不向用户暴露（`root_path` / `git_root` / `sandbox` 等响应字段脱敏，编译日志等输出前缀改写为相对路径） | [multi-user.md](docs/multi-user.md)、[deployment.md](docs/deployment.md) |
| 多用户认证 | argon2id 密码哈希、HttpOnly 会话 Cookie（库只存 SHA-256）、注册审批流、登录限流、管理员引导（env/CLI） | [backend-core.md](docs/backend-core.md) |
| 注册自动审批（IP 策略） | 注册记录真实客户端 IP（可信代理解析 X-Forwarded-For）；同 IP 账号上限 + CIDR 白名单 + 离线地理白名单叠加判定「无恶意」即自动通过，否则转人工待审批；地理查询 ip2region（.xdb，中国命中直接采用，国内精度更高）优先、非/未知中国回落 GeoLite2；每 IP 注册限流，只降级不拒绝 | [backend-core.md](docs/backend-core.md)、[deployment.md](docs/deployment.md) |
| 多用户项目与配额 | 上传 zip/7z/rar 安全解压（rar 经 unar 优先、bsdtar 回退，解压命令经 SandboxRunner 沙箱执行：convert 镜像内置双工具 + 压缩包只读挂载 + 宿主监视任务超配额/超时硬中断，local 回落探测宿主 PATH）、项目命名去重、项目空间配额记账、回收站（7 天）、打包下载；写回超限回滚 | [backend-core.md](docs/backend-core.md)、[multi-user.md](docs/multi-user.md) |
| 多用户权限协作 | 协作者管理（按用户名添加/改角色/移除）、项目转交（原 owner 变 viewer 或退出）、viewer 写保护；权限实时查库即时生效 | [backend-core.md](docs/backend-core.md)、[multi-user.md](docs/multi-user.md) |
| Git 版本管理 | 上传自动/版本页手动 git init（LaTeX 专用 .gitignore，项目已有则尊重）；.git 不占项目配额，改按 git_dir_max_mb 独立上限（超限拒绝上传/初始化/提交）；「已初始化」以项目根自带 .git 为准；历史条目经 URL 参数预填 git_diff 对比 | [git_manage.md](docs/modules/git_manage.md) |
| 模板体系 | 内置模板（包内资源，manifest 元数据 + 预编译缩略图/PDF 预览）与用户私有库双模式并存；从模板创建项目为纯复制语义（清洗剔除编译产物/.git/库元数据，无变量替换）；上传走安全解压 + 扩展名白名单，模板库本体占「模板」衍生配额、创建项目走项目配额 | [template_library.md](docs/modules/template_library.md)、[template_gen.md](docs/modules/template_gen.md) |
| 共享项目反向补丁撤销 | 多用户模式写路径统一记账（workspace_edit_ops + .edit_logs 快照），undo/redo 只回退当前用户自己的修改：冲突即拒绝（409）、恢复超配额拒绝（413）、LIFO 整组回滚、redo 对称；共享项目 v1 禁令解除 | [DESIGN.md §14](DESIGN.md)、[multi-user.md](docs/multi-user.md) §8 |
| 多用户 LLM 治理 | 每用户滑动窗口限流 + 并发租约（`llm_governance.py`，单事务原子准入），超限 429 + Retry-After；登录限流同表计数；仅多用户模式生效，单用户零依赖直通 | [backend-core.md](docs/backend-core.md)、[multi-user.md](docs/multi-user.md) §7 |
| 衍生数据记账与自助清理 | 四类衍生目录用量缓存（user_derived_usage 表）+ 检查点增量刷新 + 维护循环对账纠偏，配额校验与 /me 用量 O(1)；工作台分类用量条 + 自助清理（仅本人）；过期会话删除与写作会话保留期清理并入维护循环 | [backend-core.md](docs/backend-core.md)、[multi-user.md](docs/multi-user.md) §3.3 |
| 邮件通知与自助找回 | SMTP 通知走 email_outbox 队列 + 发送协程（重试留档限速，未配置优雅降级）；密码可经 `LATEXCELLENCE_SMTP_PASSWORD` 环境变量注入（优先于配置文件且永不写盘，与 LLM Key 同一安全策略）；找回令牌只存哈希、单次有效，forgot 响应恒定语义防枚举；邮箱部分唯一索引 + profile 改绑需当前密码；配额预警迟滞并入维护循环 | [backend-core.md](docs/backend-core.md)、[multi-user.md](docs/multi-user.md) §9、[deployment.md](docs/deployment.md) |
| 多实例状态外置 | 跨请求状态（工作区绑定 / 检查上下文 / 待定交互与 DDG challenge / 维护选主）外置共享 SQLite（迁移 v8），副本扩展无需粘性会话；flock 会话互斥、config 热重载、stdout 日志开关；compose 编排 + 冒烟脚本 | [DESIGN.md §15](DESIGN.md)、[deployment.md](docs/deployment.md)、[backend-core.md](docs/backend-core.md) |
| 多用户前端 | 登录/注册/待审批页、项目工作台（上传 zip·7z·目录、配额、回收站、权限）、管理面板、路由守卫、auth 切片；多用户模式固定同源 + CSRF 头 + Cookie 会话，SSE 不再追加 token | [frontend.md](docs/frontend.md)、[multi-user.md](docs/multi-user.md) |
| i18n 多语言 | i18next 简中 / 繁中 / 英语，按 `common` / `pages` / `components` / `hooks` / `errors` 命名空间拆分 | [frontend.md](docs/frontend.md) |

各功能模块的单模块设计（数据流、Pydantic 模型、API、配置）见 [docs/modules.md](docs/modules.md)。
