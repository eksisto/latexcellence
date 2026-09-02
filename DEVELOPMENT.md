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
| [docs/prompt-optimization-plan.md](docs/prompt-optimization-plan.md) | 提示词优化计划（Qwen3.6-35B-A3B） |
| [docs/windows-temp-permissions.md](docs/windows-temp-permissions.md) | Windows 下 Codex 沙箱临时目录权限问题排查（pytest basetemp / ACL / 清理） |
| [docs/web-access-operations.md](docs/web-access-operations.md) | Writing Chat 网络访问灰度、观测、容量检查、缓存维护与回滚 |
| [docs/writing-chat-web-access-architecture.md](docs/writing-chat-web-access-architecture.md) | Writing Chat 网络访问架构：搜索、学术检索、分级抓取、结构化抽取与证据链 |
| [docs/showcase.md](docs/showcase.md) | 独立展示页 |
| [docs/multi-user-plan.md](docs/multi-user-plan.md) | 多用户模式设计方案（双模式/认证/用户空间/配额/权限协作/管理后台/迁移） |
| [docs/multi-user-v2-plan.md](docs/multi-user-v2-plan.md) | 多用户模式 v2 设计方案（Docker 沙箱、rar、共享撤销反向补丁、分片上传、SMTP、衍生记账优化、多实例部署） |
| [docs/rar-extractor-fallback.md](docs/rar-extractor-fallback.md) | RAR 解压器回退层设计与实施记录（unar → bsdtar，含 Alpine 适配评估、文件名归一化与部署步骤） |
| [docs/editor-plan.md](docs/editor-plan.md) | 网页编辑器功能设计方案（CM6 编辑器、LaTeX 命令中/拼/英快速输入、编辑器侧 AI 助手；已实施，设计共识与决策记录） |
| [docs/editor-productivity-plan.md](docs/editor-productivity-plan.md) | 编辑器生产力改造设计方案（多缓冲区 tab、大纲导航、引用补全、格式化、KaTeX 预览、编译错误跳转；共识已定稿未实施，grilling 决策记录 Q1–Q19） |
| [docs/multi-model-plan.md](docs/multi-model-plan.md) | 多模型支持设计方案（模型档案 + 角色槽位路由、能力硬校验、推理模型封装层兼容；已实施） |
| [docs/modules/editor.md](docs/modules/editor.md) | 网页编辑器模块文档（整文件读写、命令快速输入、编译联动、AI 助手侧栏与保鲜横幅的数据流 / API / 配置 / 前端结构） |
| [docs/modules/template_library.md](docs/modules/template_library.md) | 模板库模块文档（内置模板清单、双模式数据流、manifest schema、API 一览、安全边界、配额并入、清洗规则与维护者脚本） |
| [docker/README.md](docker/README.md) | Docker 沙箱镜像体系（四镜像构建、多架构、离线导入导出、缺宏包排查） |
| [docs/deployment.md](docs/deployment.md) | 后端部署指南（单用户/多用户单进程/多实例 Compose 部署、systemd 托管、HTTPS 反向代理、安全风险声明、备份、环境变量与升级回滚） |
| [tools/visual_review_api.py](tools/visual_review_api.py) | 视觉评审工具的 Chat Completions → Responses API 请求适配 |
| [tools/compose-smoke.sh](tools/compose-smoke.sh) | 多实例 Compose 冒烟（replicas=2：注册审批 → 分片上传 → undo → 下载；`--with-tex` 追加编译冒烟） |
| [tools/bump-version.sh](tools/bump-version.sh) | 版本号一键升级（同步 main.py 与前端三语 i18n；支持 major/minor/patch 或显式版本号，默认以 `chore(release)` 自动提交，`--no-commit` 跳过） |
