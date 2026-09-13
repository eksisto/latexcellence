# LaTeXcellence — Project Summary

> AI 快速索引：先在本文件定位模块与设计要点，再到 `docs/` 读细节。
> 项目结构变更（新增/删除模块、核心文件路径变更）时，**必须**同步更新本文件。
>
> 技术栈速查、常用命令、编码约束见 [AGENTS.md](AGENTS.md)；
> 完整设计见 [DESIGN.md](DESIGN.md)；开发文档入口见 [DEVELOPMENT.md](DEVELOPMENT.md)。

## 目录结构

```
latexcellence/
├── main.py                # FastAPI 入口 + 静态文件挂载
├── config.json            # 运行配置（模板见 config.example.json）
├── backend/
│   ├── core/              # 配置热重载、.env 环境变量加载、模型路由、LLM 客户端、日志、workspace/project 管理、
│   │                      # 集中提示词 prompts.py、逐段检查基类 base_paragraph_check.py、
│   │                      # 访问控制、安全中间件、多用户基础设施 multiuser/
│   ├── utils/             # LaTeX 解析（marker 保护、\input 展开）、json_utils、latex_json_guard、
│   │                      # path_guard（路径安全唯一入口）、tex_compile、沙箱 sandbox/、
│   │                      # 联网搜索 web_search/、学术检索 scholarly_research/、
│   │                      # Function Calling 工具 project_tools/、宏包检测与规则表
│   └── modules/           # 各功能模块（见下表），router.py 薄路由 + service.py 业务逻辑
├── frontend/
│   └── src/               # pages / components（editor、settings 等子目录）/ hooks /
│                          # lib/editor（CM6 纯模块与自研 Lezer 语法）/ store / api / i18n / data
├── docs/                  # 开发文档（模块明细在 docs/modules/，设计记录在各 *-design.md）
├── tests/                 # pytest：test_api / test_multiuser / test_utils / test_docker 等
│                          #（docker marker 无 daemon 自动跳过）
├── tools/                 # 维护者专用：各模块人工评估、评测回归 CI（tools/ci）、宏包规则验证、Agent 评测工作区
├── scripts/               # 维护脚本：check.sh 统一验证入口（委托 check_backend.sh /
│                          # check_frontend.sh，CI 门禁同源调用）、check_conventions.py、sync_config.py
├── docker/                # Docker 沙箱镜像体系（backend/tex/convert/plot 四镜像）
├── data/                  # 运行期数据（DB、用户空间、模板、缓存；运行时生成）
└── static/                # Vite 构建产物
```

## 功能模块速查

| 模块 | 职责 | LLM | 文档 |
| --- | --- | --- | --- |
| auth | 多用户认证与管理（/api/auth、/api/admin） | ✗ | [multi-user.md](docs/multi-user.md) |
| projects | 多用户项目：上传/下载/回收站/配额/协作 | ✗ | [multi-user.md](docs/multi-user.md) |
| typo_check | 错字查找 | ✓ | [typo_check.md](docs/modules/typo_check.md) |
| expression_opt | 学术表达优化 | ✓ | [expression_opt.md](docs/modules/expression_opt.md) |
| terminology_check | 术语一致性检查 | ✓ | [terminology_check.md](docs/modules/terminology_check.md) |
| paragraph_translate | 逐段翻译 | ✓ | [paragraph_translate.md](docs/modules/paragraph_translate.md) |
| code_review | LaTeX 代码审查（审格式化后源码，无 marker） | ✓ | [code_review.md](docs/modules/code_review.md) |
| compile | 编译 + 错误翻译 + SyncTeX 正反向只读查询 | ✓(翻译) | [compile.md](docs/modules/compile.md) |
| git_diff | Git 版本差异 PDF | ✓(翻译) | [git_diff.md](docs/modules/git_diff.md) |
| git_manage | Git 版本管理（初始化/提交/历史 + 源码级变更查看与选择性恢复，与 git_diff 共享执行层） | ✗ | [git_manage.md](docs/modules/git_manage.md) |
| template_gen | 模板生成自主 Agent（沙箱闭环） | ✓ | [template_gen.md](docs/modules/template_gen.md) |
| template_library | 内置 + 用户模板库，从模板创建项目 | ✗ | [template_library.md](docs/modules/template_library.md) |
| formula_ocr | 图片 → LaTeX 公式 | ✓(Vision) | [formula_ocr.md](docs/modules/formula_ocr.md) |
| table_ocr | 图片 → LaTeX 表格 | ✓(Vision) | [table_ocr.md](docs/modules/table_ocr.md) |
| format_converter | 格式转换（pandoc） | ✓(兜底) | [format_converter.md](docs/modules/format_converter.md) |
| style_modify | 粘贴代码 / 项目 Agent 样式修改 | ✓ | [style_modify.md](docs/modules/style_modify.md) |
| writing_chat | 写作助手对话（联网搜索、学术检索、工具确认） | ✓ | [writing_chat.md](docs/modules/writing_chat.md) |
| bib_manager | 参考文献管理 + DOI 多数据源查询 | 部分 | [bib_manager.md](docs/modules/bib_manager.md) |
| plot_gen | 科研绘图 Agent（数据 → Python → TikZ） | ✓ | [plot_gen.md](docs/modules/plot_gen.md) |
| editor | 网页编辑器（整文件读写、多缓冲区、编译联动、AI 助手、文件树右键菜单与重命名、资源上传、拖拽移动与目录组织） | ✓(助手) | [editor.md](docs/modules/editor.md) |

## 核心设计要点

| 设计 | 一句话 | 详细文档 |
| --- | --- | --- |
| Marker 保护 | LaTeX 结构替换为 marker，LLM 只能改纯文本；还原按创建顺序降序；code_review 例外 | [DESIGN.md §2](DESIGN.md)、[backend-core.md](docs/backend-core.md) |
| JSON 输出校验流水线 | `safe_parse_json()` → Pydantic `model_validate()` → 失败反馈 LLM 重试 | [DESIGN.md §3](DESIGN.md) |
| LaTeX×JSON 转义防护 + 分隔符协议 | `latex_json_guard` 拦截转义损坏；OCR/编辑类用 `<<<LATEX>>>` 直传纯文本 | [backend-core.md](docs/backend-core.md) |
| 集中提示词管理 | 全部提示词在 `backend/core/prompts.py`，经 `get_prompt()` 取用 | [prompt-guidelines.md](docs/prompt-guidelines.md) |
| 多模型路由 | 模型档案 + 角色槽位，`get_client_for(role)` 两级解析，热重载清池 | [multi-model.md](docs/multi-model.md) |
| CheckOperationContext | 每次检查生成 operation_id + marker 映射 + MD5 快照，apply 精确还原 | [DESIGN.md §1.4](DESIGN.md) |
| 乐观锁 | 读取返回文件 MD5，写回校验，不一致抛 409 | [DESIGN.md §4.2](DESIGN.md) |
| SSE 流式 | 段落检查 / Agent / 写作聊天统一 progress / result / error / done 事件 | [modules.md](docs/modules.md) |
| Function Calling 工具体系 | ToolRegistry 全局注册 + Agent 会话私有工具；结果带 shown/total/next_step | [function-calling-tools.md](docs/function-calling-tools.md) |
| Docker 沙箱 | 外部命令统一走 SandboxRunner（docker/local 双后端，auto 探测回落） | [backend-core.md](docs/backend-core.md)、[deployment.md](docs/deployment.md) |
| 多用户双模式 | `multi_user.enabled` 开关；服务器路径不向用户暴露 | [multi-user.md](docs/multi-user.md) |
| 共享项目反向补丁撤销 | 写路径统一记账，undo/redo 只回退本人修改 | [DESIGN.md §14](DESIGN.md) |
| 逐段检查前端流程 | `useParagraphCheck` hook 统一封装，`useResultFilter` 排序过滤 | [frontend.md](docs/frontend.md) |
| 检查结果保鲜 | 检查时记录 MD5，不一致显示 StaleCheckBanner 提示重查 | [frontend.md](docs/frontend.md) |
| 编辑器 AI 助手 | 复用 Writing Chat 基础设施；建议只改缓冲区；工具白名单 + 确认卡 | [editor.md](docs/modules/editor.md) |

各模块详细设计（数据流、模型、API、配置）见 [docs/modules.md](docs/modules.md) 索引。
