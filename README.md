# LaTeXcellence

<img align="right" width="150" height="150" alt="latexcellence logo" src="frontend/public/favicon.svg">

让技术退居幕后，让思想落于纸上。

LaTeXcellence 是一个面向高校理工科学生与科研团队的 AI 学术写作助手：排版、编译、报错、引用、图表这些「工具摩擦」交给 AI，用户只负责研究本身。

## 目录

- [作品定位](#作品定位)
- [核心功能](#核心功能)
- [界面截图](#界面截图)
- [快速开始](#快速开始)
- [基本用法](#基本用法)
- [多用户与部署](#多用户与部署)
- [技术栈](#技术栈)
- [项目结构](#项目结构)
- [文档](#文档)
- [鸣谢](#鸣谢)

## 作品定位

理工科论文写作长期被三件事拖住：LaTeX 排版门槛高、文献检索与引用繁琐、学术表达与格式规范需要反复校对。LaTeXcellence 的定位是**个人与团队共用的学术写作全流程助手**——单用户模式开箱即用，本地运行、数据不出本机；开启多用户模式后即可作为团队协作平台，提供注册审批、独立项目空间与权限协作（见[多用户与部署](#多用户与部署)）。

核心主张三件事：

- **会自主干活**：写作助手、模板生成、科研绘图、LaTeX 样式修改都是真正的 Agent——规划、调用工具、执行、验证、出错自修，全程可回放。
- **修改始终可控**：LaTeX 结构经 Marker 保护，LLM 只能修改纯文本；写回前经 JSON 校验、内容校验、MD5 乐观锁与原子写入，保证源文件安全。
- **可靠可验证**：LLM 输出经结构化校验与重试闭环；内置人工评测工具，效果有数据支撑。

选择 LaTeX，是因为它在 AI 时代反而是最优接口：LaTeX 源码是纯文本，章节、公式、引用都以显式命令表达，AI 可以直接读写、逐段修改，并借助编译低成本地自动验证结果；Word 把结构藏在 GUI 与复杂的文档格式里，AI 难以直接读写，更无法自动验证修改。纯文本还天然兼容 git 版本管理与 diff 审阅，本项目的逐段审校、模板生成、科研绘图都建立在这个闭环之上。

典型场景：毕业论文、数学建模与课程报告、期刊初稿，以及作为课程辅助的 LaTeX 教学（编译错误翻译让新手看得懂报错）。

## 核心功能

按论文写作流程组织，各模块的完整功能与设计细节见 [docs/modules.md](docs/modules.md)。

### 写作与检索

- **写作助手**：上下文感知的多轮对话，能直接修改指定段落；支持深度思考、提示词预设、会话持久化与流式回复，上下文超限时自动压缩历史
- **网页编辑器**：多标签 LaTeX 编辑——智能补全、大纲导航、`\ref` / `\cite` 跨文件引用补全、实时语法诊断、一键编译 + PDF 预览 + SyncTeX 正反向同步，文件树支持右键菜单（重命名 / 新建 / 删除 / 上传 / 设为主文件）与拖拽移动等目录组织操作，内置 AI 助手侧栏（详见 [docs/modules/editor.md](docs/modules/editor.md)）
- **联网检索与学术研究**：agentic 网页搜索（搜索 → 按需读页 → 页内查找，多引擎可选）+ 多 Provider 学术论文检索，回复引用自动校验

### 审校与质检

- **错字查找**：逐段检查拼写错误，定位并修正，保留 LaTeX 结构不变
- **表达优化**：按特定风格（清晰、简洁、学术语气等）优化表达，支持自定义提示词
- **术语一致性检查**：LLM 提取全文术语，语义聚类识别大小写、中英混用、缩写变体等不一致，支持自定义替换
- **LaTeX 代码审查**：先工具格式化，再由 LLM 审查代码质量并给出只读建议（不写回文件）
- **自动编译**：一键调用 latexmk 编译，错误信息由 LLM 翻译为通俗解释并给出修复建议
- 以上检查均支持**多文件展开**：递归解析 `\input` / `\include` 引用，一键处理整个项目的多个 `.tex` 文件

### 排版与生成

- **模板生成**：自主 Agent 从自然语言描述产出可编译的多文件 LaTeX 工程——写文件、编译验证、读日志修错，循环直至构建成功，生成后一键下载 ZIP
- **模板库**：内置论文、学位论文、报告、幻灯片等多种模板，一键创建为新项目；支持上传 zip 建私有模板、当前项目另存为模板
- **科研绘图**：上传 CSV/Excel/JSON 数据，自动完成数据画像、图表类型推荐、Python 绘图脚本生成与沙箱执行，输出矢量图与 TikZ 源文件，支持自然语言微调
- **公式 / 表格识别**：上传或粘贴图片，通过视觉模型识别为 LaTeX 代码（自动判定行内/行间，表格支持三线表、合并单元格等浮动体）
- **源码格式化**：优化 LaTeX 源码的间距、缩进、环境对齐
- **LaTeX 样式修改**：Agent 自动定位项目中的样式定义，生成逐文件 diff 与影子编译结果，确认后批量事务写回，并提供安全撤销

### 文献与引用

- **参考文献管理**：识别参考文献格式规则，从文本或截图提取结构化元数据，自动生成规范 BibTeX 并写入项目 `.bib` 文件（多步向导交互，格式样式可保存复用）

### 更多能力

- **多模型支持**：设置页可配置多个模型档案，按任务槽位自动路由——识别类用视觉模型、逐段检查用快速模型、Agent 与写作助手用推理模型（详见 [docs/multi-model.md](docs/multi-model.md)）
- **逐段翻译**：按段落将内容翻译为目标语言，保留 LaTeX 结构不变
- **Git 版本管理**：项目一键初始化/提交与历史浏览；可将两个 Git 状态的差异编译为红删绿增底纹的 PDF（不影响项目源码）
- **Markdown 转 LaTeX**：粘贴 Markdown 文本，调用 Pandoc 转换为 LaTeX 代码（需系统安装 Pandoc）

## 界面截图

<table>
  <tr>
    <td><a href="screenshots/0.jpg"><img src="screenshots/0.jpg" alt="界面截图 01" width="320"></a></td>
    <td><a href="screenshots/1.jpg"><img src="screenshots/1.jpg" alt="界面截图 02" width="320"></a></td>
    <td><a href="screenshots/2.jpg"><img src="screenshots/2.jpg" alt="界面截图 03" width="320"></a></td>
    <td><a href="screenshots/3.jpg"><img src="screenshots/3.jpg" alt="界面截图 04" width="320"></a></td>
    <td><a href="screenshots/4.jpg"><img src="screenshots/4.jpg" alt="界面截图 05" width="320"></a></td>
    <td><a href="screenshots/5.jpg"><img src="screenshots/5.jpg" alt="界面截图 06" width="320"></a></td>
    <td><a href="screenshots/6.jpg"><img src="screenshots/6.jpg" alt="界面截图 07" width="320"></a></td>
    <td><a href="screenshots/7.jpg"><img src="screenshots/7.jpg" alt="界面截图 08" width="320"></a></td>
    <td><a href="screenshots/8.jpg"><img src="screenshots/8.jpg" alt="界面截图 09" width="320"></a></td>
    <td><a href="screenshots/9.jpg"><img src="screenshots/9.jpg" alt="界面截图 10" width="320"></a></td>
    <td><a href="screenshots/10.jpg"><img src="screenshots/10.jpg" alt="界面截图 11" width="320"></a></td>
    <td><a href="screenshots/11.jpg"><img src="screenshots/11.jpg" alt="界面截图 12" width="320"></a></td>
    <td><a href="screenshots/12.jpg"><img src="screenshots/12.jpg" alt="界面截图 13" width="320"></a></td>
    <td><a href="screenshots/13.jpg"><img src="screenshots/13.jpg" alt="界面截图 14" width="320"></a></td>
    <td><a href="screenshots/14.jpg"><img src="screenshots/14.jpg" alt="界面截图 15" width="320"></a></td>
  </tr>
</table>

项目内置一个在线展示页（滚动叙述式落地页），本地运行方式见 [docs/showcase.md](docs/showcase.md)。

## 快速开始

### 前置条件

- Python 3.12+
- Node.js 20+（仅构建前端时需要，见 `frontend/package.json` 的 `engines` 约束）
- texlive（含 xelatex、latexmk、latexindent、latexdiff，编译功能、LaTeX 代码审查功能、git diff 功能需要）
- Pandoc（Markdown 转 LaTeX 功能需要，[下载安装](https://pandoc.org/installing.html)）
- OpenAI API Key（或兼容的 API 端点）

### 安装

```bash
# 克隆项目
git clone https://github.com/eksisto/latexcellence.git latexcellence
cd latexcellence

# 建议使用虚拟环境，避免污染系统 Python
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 安装前端依赖
cd frontend
npm install
cd ..
```

### 配置

```bash
# 复制配置模板
cp config.example.json config.json
```

可以在 `config.json` 中填入 `api_key`，也可以使用环境变量 `LX_LLM_KEY`（推荐，优先级高于配置文件）。SMTP 密码、Tavily 搜索 Key、学术数据源 Key 等同理支持环境变量注入，且注入值永不写盘，完整清单见 [docs/deployment.md](docs/deployment.md)「环境变量参考」。

学术文献检索默认关闭，可在设置页启用各学术 Provider（默认数据源为 OpenAlex，[Key 获取](https://openalex.org/settings/api)）。

### 启动

**开发模式**（推荐）：

```bash
# 终端 1：启动后端
python main.py config.json

# 终端 2：启动前端
cd frontend
npm run dev
```

打开 http://localhost:5173

**生产模式**：

```bash
# 1. 编辑 config.json，将 server.dev_mode 设为 false（关闭后才会挂载 static/ 目录）
# 2. 构建前端
cd frontend
npm run build
cd ..
# 3. 启动后端
python main.py config.json
```

打开 http://localhost:8000

**前后端分离部署**：前端作为静态站点部署到公网服务器，后端保留在用户本机（`127.0.0.1` + 访问令牌），用户的 `.tex` 文件、TeX 工具链和 API Key 全部留在本地，前端服务器不接触用户数据。配置步骤（`security.access_token`、`server.allowed_origins`、构建时用 `VITE_API_BASE_URL` 固化默认后端地址）见 [docs/development.md](docs/development.md)「开发/生产模式」。

## 基本用法

1. 打开本地 LaTeX 项目目录
2. 选择 .tex 文件，系统解析段落
   - 开启 `包含子文件` 后，自动递归展开 `\input`/`\include` 引用的子文件
3. LLM 在纯文本上执行校对/优化
   - LaTeX 结构被替换为不可见的 marker，LLM 不能修改它们
4. 用户审核 diff 结果，逐项确认
5. 确认后，系统将 marker 还原为 LaTeX 并原子性写回源文件

## 多用户与部署

- **多用户模式**：`multi_user.enabled` 一键开启，提供注册/审批、会话登录、独立项目空间与配额、owner/editor/viewer 权限协作、回收站与存量数据迁移（详见 [docs/multi-user.md](docs/multi-user.md)）
- **服务器部署**：多用户模式的构建启动、管理员引导、HTTPS 反向代理、数据备份与日常运维见 [docs/deployment.md](docs/deployment.md)；注册支持按 IP 策略自动审批
- **Docker 沙箱**：全部外部命令（编译、git、pandoc、绘图等）统一经 SandboxRunner，可进一次性容器隔离执行；`sandbox.mode=auto` 探测到 Docker 即自动启用，镜像体系见 [docker/README.md](docker/README.md)
- **水平扩展**：跨请求状态外置共享 SQLite，无需粘性会话；仓库根 `docker-compose.yml` 支持 `--scale app=N` 弹性扩缩容（详见 [docs/deployment.md](docs/deployment.md)）

## 技术栈

| 层 | 技术 |
| --- | --- |
| 后端 | Python 3.12+, FastAPI, uvicorn, openai, pydantic v2, httpx |
| 前端 | React 19, Vite 6, Tailwind CSS 4, Zustand 5, shadcn/ui, sonner, react-markdown + KaTeX, highlight.js |
| 测试 | pytest + pytest-asyncio；前端 node:test（`npm test`，Node 内置测试运行器） |

## 项目结构

```
latexcellence/
├── main.py                  # 统一入口
├── config.json              # 配置文件（模板见 config.example.json）
├── docker-compose.yml       # 多实例编排（副本扩展 + 可选 Caddy 反代）
├── backend/
│   ├── core/                # 核心：配置、LLM 客户端、日志、工作区管理、项目管理
│   ├── modules/             # 各功能模块（路由 + 业务逻辑）
│   └── utils/               # 工具：文件操作、JSON 校验、LaTeX 解析等
├── docker/                  # 沙箱镜像体系（详见 docker/README.md）
├── frontend/
│   └── src/                 # React 应用
├── showcase/                # 独立展示页（零依赖静态站）
├── screenshots/             # 项目截图
├── data/                    # 应用运行期数据（运行时生成）
├── tests/                   # 测试
├── tools/                   # 运维与评估工具
├── docs/                    # 项目文档
└── clean-cache.sh / clean-cache.ps1  # 清理缓存脚本
```

## 文档

| 文档 | 内容 |
| --- | --- |
| [docs/development.md](docs/development.md) | 开发者上手：技术栈、目录结构、启动、配置、编码规范 |
| [DEVELOPMENT.md](DEVELOPMENT.md) | 开发文档入口（索引至 `docs/` 详细文档） |
| [DESIGN.md](DESIGN.md) | 架构、数据流与核心设计 |
| [docs/modules.md](docs/modules.md) | 功能模块索引与各模块设计文档 |
| [docs/multi-user.md](docs/multi-user.md) | 多用户模式设计 |
| [docs/deployment.md](docs/deployment.md) | 部署指南（多用户服务器部署、环境变量参考） |
| [AGENTS.md](AGENTS.md) | AI 编码协作规范 |
| [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) | 第三方依赖与许可证 |

维护：`clean-cache.sh`（Windows 下可用 `clean-cache.ps1`）可清理 `__pycache__`、`.pytest_cache`、前端构建缓存等。

## 鸣谢

感谢 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) 中这些开源项目的贡献者。
