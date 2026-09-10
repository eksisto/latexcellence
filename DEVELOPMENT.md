# LaTeXcellence — 项目开发文档

> 本文件为开发文档索引。详细内容在 `docs/` 目录下。

**各文档入口**：

| 文档 | 内容 |
| --- | --- |
| [docs/development.md](docs/development.md) | 项目概况、技术栈、目录结构、启动、配置、编码规范 |
| [docs/backend-core.md](docs/backend-core.md) | 后端核心模块：配置、AI 客户端、日志、项目管理等 |
| [docs/modules.md](docs/modules.md) | 所有功能模块的文档索引 |
| [docs/modules/](docs/modules/) | 各模块的详细文档 |
| [docs/frontend.md](docs/frontend.md) | 前端开发文档 |
| [docs/testing.md](docs/testing.md) | 测试规范 |
| [docs/function-calling-tools.md](docs/function-calling-tools.md) | Function Calling 工具清单（全局/私有工具、配置结构） |
| [docs/duckduckgo-challenge.md](docs/duckduckgo-challenge.md) | DDG 验证码处理与前端交互说明 |
| [docs/templategen-optimization-lessons.md](docs/templategen-optimization-lessons.md) | template_gen 闭环优化复盘与通用性门禁说明 |
| [docs/plotgen-optimization-lessons.md](docs/plotgen-optimization-lessons.md) | plot_gen 闭环优化复盘与最佳实践说明 |
| [docs/prompt-guidelines.md](docs/prompt-guidelines.md) | 提示词工程原则与约定（小型模型可迁移原则、统一结构、回归口径） |
| [docs/windows-temp-permissions.md](docs/windows-temp-permissions.md) | Windows 下 Codex 沙箱临时目录权限问题排查（pytest basetemp / ACL / 清理） |
| [docs/web-access-operations.md](docs/web-access-operations.md) | Writing Chat 网络访问灰度、观测、容量检查、缓存维护与回滚 |
| [docs/writing-chat-web-access-architecture.md](docs/writing-chat-web-access-architecture.md) | Writing Chat 网络访问架构：搜索、学术检索、分级抓取、结构化抽取与证据链 |
| [docs/showcase.md](docs/showcase.md) | 独立展示页 |
| [docs/multi-user.md](docs/multi-user.md) | 多用户模式设计（双模式、认证、用户空间与配额、权限协作、上传管线、Docker 沙箱、LLM 治理、反向补丁撤销、SMTP、多实例；v1+v2 合并的权威设计参考） |
| [docs/rar-extractor-fallback.md](docs/rar-extractor-fallback.md) | RAR 解压器回退层设计（unar → bsdtar：选择层、文件名归一化、错误码映射、fixtures 与实测结论） |
| [docs/editor-design.md](docs/editor-design.md) | 网页编辑器 v1 设计（CM6 编辑器、LaTeX 命令中/拼/英快速输入、编辑器侧 AI 助手；已实施，设计与决策记录） |
| [docs/editor-productivity-design.md](docs/editor-productivity-design.md) | 编辑器生产力改造 v2 设计（多缓冲区 tab、大纲导航、引用补全、格式化、KaTeX 预览、编译错误跳转、修改行色条；已实施，设计与决策记录 Q1–Q19） |
| [docs/editor-experience-design.md](docs/editor-experience-design.md) | 编辑器写作体验收口设计方案（编辑器 v3：SyncTeX 正反向同步、用户自定义片段库、实时语法诊断、跨文件大纲与 KaTeX 预览卡、编辑器助手接入联网搜索与学术检索、自研 MIT Lezer 子集语法；已实施，设计与决策记录） |
| [docs/editor-file-upload-design.md](docs/editor-file-upload-design.md) | 编辑器文件树上传任意格式资源设计（决策记录 D1–D12、安全护栏、上传校验链、树扫描产物黑名单与插入引用；已实施） |
| [docs/editor-file-move-design.md](docs/editor-file-move-design.md) | 编辑器文件树拖拽移动设计（移动文件/文件夹、新建与删除文件夹；决策记录 Q1–Q13、安全护栏、主文件跟随、标签页路径重映射、错误码与测试基线；已实施） |
| [docs/editor-file-context-menu-design.md](docs/editor-file-context-menu-design.md) | 编辑器文件树右键菜单设计（自定义上下文菜单、按目标类型菜单矩阵、重命名对话框、行内图标收敛；决策记录 Q1–Q13；已实施） |
| [docs/multi-model.md](docs/multi-model.md) | 多模型支持设计（模型档案 + 角色槽位路由、能力硬校验、推理模型封装层兼容；已实施） |
| [docs/deploy-followups.md](docs/deploy-followups.md) | 多用户部署侧收口设计（rar 解压器纳入 SandboxRunner、真实压缩 RAR 样本、沙箱容器预创建池 warm pool、模型连通性测试；已实施） |
| [docs/modules/editor.md](docs/modules/editor.md) | 网页编辑器模块文档（整文件读写、命令快速输入、编译联动、AI 助手侧栏与保鲜横幅的数据流 / API / 配置 / 前端结构） |
| [docs/modules/template_library.md](docs/modules/template_library.md) | 模板库模块文档（内置模板清单、双模式数据流、manifest schema、API 一览、安全边界、配额并入、清洗规则与维护者脚本） |
| [docker/README.md](docker/README.md) | Docker 沙箱镜像体系（四镜像构建、多架构、离线导入导出、缺宏包排查） |
| [docs/deployment.md](docs/deployment.md) | 后端部署指南（单用户/多用户单进程/多实例 Compose 部署、systemd 托管、HTTPS 反向代理、安全风险声明、备份、环境变量与升级回滚） |
| [docs/eval-regression-ci.md](docs/eval-regression-ci.md) | 评测回归基线 CI（tools/ 人工评测接入 Gitea Actions、指标采集与基线比对、基线治理与阈值调优） |
| [tools/visual_review_api.py](tools/visual_review_api.py) | 视觉评审工具的 Chat Completions → Responses API 请求适配 |
| [tools/compose-smoke.sh](tools/compose-smoke.sh) | 多实例 Compose 冒烟（replicas=2：注册审批 → 分片上传 → undo → 下载；`--with-tex` 追加编译冒烟） |
| [tools/bump-version.sh](tools/bump-version.sh) | 版本号一键升级（同步 main.py 与前端三语 i18n；支持 major/minor/patch 或显式版本号，默认以 `chore(release)` 自动提交，`--no-commit` 跳过） |
