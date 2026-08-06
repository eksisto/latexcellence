# LateXcellence

让排版隐于无形，让思考回归本身。LateXcellence 是一个由 LLM 驱动的 LaTeX 文档助手。后端 FastAPI + 前端 React/Vite，通过 LLM 对 `.tex` 文件进行智能处理。

## 功能

- **文本校对**：逐段检查拼写错误，定位并修正，保留 LaTeX 结构不变
- **表达优化**：按特定风格（如清晰、简洁、学术语气）优化表达，支持自定义提示词
- **术语一致性检查**：LLM 提取全文术语，语义聚类识别不一致（大小写、中英混用、缩写变体），支持自定义替换
- **公式识别**：上传公式图片，通过 LLM Vision API 识别为 LaTeX 代码
- **表格识别**：上传表格图片，通过 LLM Vision API 识别为标准 LaTeX 浮动体代码（含三线表、合并单元格等）
- **模板生成**：自主 Agent 基于自然语言描述，自动规划并产出可编译的多文件 LaTeX 工程——逐个写文件、调用 latexmk 编译验证、读日志修错、再编译，直至项目可成功构建；生成后支持一键下载 ZIP 压缩包，过期沙箱自动清理
- **科研绘图 Agent**：上传 CSV/Excel/JSON 数据，自动完成数据画像、图表类型推荐、Python 绘图脚本生成与沙箱执行，输出 PDF/SVG 矢量图和 TikZ 源文件，并支持自然语言微调
- **源码格式化**：优化 LaTeX 源码的间距、缩进、环境对齐
- **自动编译**：一键调用 latexmk 编译 TeX，错误信息由 LLM 翻译为通俗解释并给出修复建议
- **Git 差异高亮**：选择主文件和两个 Git 状态，将文档差异编译为红删绿增底纹的 PDF（不影响项目源码）
- **Markdown 转 LaTeX**：粘贴 Markdown 文本，调用 Pandoc 转换为 LaTeX 代码（需系统安装 Pandoc）
- **LaTeX 样式修改**：粘贴 LaTeX 代码并用自然语言描述修改需求，LLM 自动执行样式调整（表格对齐、文字样式、图片排版等）
- **写作助手 Chat**：上下文感知的多轮对话，能直接修改指定段落；支持联网搜索、OpenAlex 学术论文检索、引文追踪和开放获取定位；深度思考折叠框、助手提示词预设、会话持久化、Markdown + KaTeX 渲染、流式回复与结构化编辑建议
- **参考文献管理**：识别参考文献格式规则，从文本或截图提取结构化元数据，自动生成规范的 BibTeX 代码并写入项目 .bib 文件；支持元数据缺失字段告警和 BibTeX 代码编辑
- **多文件 \input 展开**：文本校对、表达优化、源码格式化、术语一致性检查均支持递归解析 `\input`/`\include` 引用，一键处理整个项目的多个 `.tex` 文件

### 交互体验

- **Toast 通知**：所有操作成功/失败均有即时反馈（Apply、编译、项目打开/关闭、配置保存、格式识别低置信度警告等）
- **参考文献管理四步向导**：格式识别 → 元数据提取（可编辑表单） → BibTeX 生成（可编辑代码） → 写入 .bib 文件；格式可保存为 JSON 文件复用，LLM 识别结果可手动修正
- **撤销/重做**：工作区顶栏按钮 + Ctrl+Z / Ctrl+Shift+Z 键盘快捷键，支持多步撤销
- **全部应用**：检查结果可一键批量写回，带进度提示和二次确认
- **确认对话框**：关闭项目、删除提示词、全部应用等操作前弹出确认，防止误触
- **结果搜索过滤**：段落检查结果支持文本搜索、按修改类型过滤、段落编号跳转
- **多文件 Tab 分组**：开启 `包含子文件` 后，段落预览和检查结果按文件分 Tab 展示，进度条显示当前处理的文件名
- **编译后 PDF 预览**：编译成功后自动在工作区内联展示 PDF
- **图片 OCR 统一交互**：公式/表格识别共享拖拽上传、Ctrl+V 粘贴、一键复制
- **写作助手 Chat 多轮对话**：会话磁盘持久化、会话重命名、助手提示词预设、深度思考折叠框（`<thinking>` 流式解析 + 可折叠 UI）、Markdown + KaTeX 渲染、双轮协议（流式回复 + 结构化编辑建议 diff）、编辑建议手动微调、上下文压缩与删除

## 技术栈

| 层 | 技术 |
| --- | --- |
| 后端 | Python 3.12+, FastAPI, uvicorn, openai, pydantic v2, httpx |
| 前端 | React 19, Vite 6, Tailwind CSS 4, Zustand 5, shadcn/ui, sonner, react-markdown + KaTeX |
| 测试 | pytest + pytest-asyncio |

## 快速开始

### 前置条件

- Python 3.12+
- Node.js 18+
- texlive（含 xelatex、latexmk、latexindent、latexdiff，编译功能、LaTeX 代码审查功能、git diff 功能需要）
- Pandoc（Markdown 转 LaTeX 功能需要，[下载安装](https://pandoc.org/installing.html)）
- OpenAI API Key（或兼容的 API 端点）

### 安装

```bash
cd latexcellence

# 安装后端依赖
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

可以在 `config.json` 中填入 `api_key`，也可以使用环境变量 `LX_LLM_KEY`（推荐）。

环境变量 `LX_LLM_KEY` 优先级高于配置文件中的 `api_key`。

学术文献检索默认关闭。可在设置页启用并填写免费的 OpenAlex API Key；Key 可从 [OpenAlex 设置页](https://openalex.org/settings/api) 获取。

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
# 1. 将后端切换到生产模式（关闭 dev_mode 才会挂载 static/ 目录）
# 编辑 config.json，将 server.dev_mode 设为 false

# 2. 构建前端
cd frontend
npm run build
cd ..

# 3. 启动后端
python main.py config.json
```

打开 http://localhost:8000。

> 说明：前端依赖与脚本位于 `frontend/package.json`，根目录无 `package.json`。

## 基本用法

1. 打开本地 LaTeX 项目目录
2. 选择 .tex 文件，系统解析段落
   - 开启 `包含子文件` 后，自动递归展开 `\input`/`\include` 引用的子文件
3. LLM 在纯文本上执行校对/优化
   - LaTeX 结构被替换为不可见的 marker，LLM 不能修改它们
4. 用户审核 diff 结果，逐项确认
5. 确认后，系统将 marker 还原为 LaTeX 并原子性写回源文件

## 项目结构

```
latexcellence/
├── main.py                  # 统一入口
├── config.json              # 配置文件
├── config.example.json      # 配置模板
├── backend/
│   ├── core/                # 核心：配置、LLM 客户端、日志、工作区管理、项目管理
│   ├── modules/             # 各功能模块（路由 + 业务逻辑）
│   └── utils/               # 工具：文件操作、JSON 校验、LaTeX 解析等
├── frontend/
│   └── src/                 # React 应用
├── prompts/                 # 自定义提示词
├── templates/               # 生成的模板
├── tests/                   # 测试
├── docs/                    # 项目文档
└── clean-cache.sh          # 清理缓存脚本（Python/前端缓存）
```

## 维护

- `clean-cache.sh`：清理 `__pycache__`、`.pytest_cache`、前端 `node_modules/.cache` 等缓存。

## 鸣谢

[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)，感谢这些开源项目的贡献者。
