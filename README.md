# LateXcellence

让排版隐于无形，让思考回归本身。LateXcellence 是一个由 LLM 驱动的 LaTeX 文档助手。后端 FastAPI + 前端 React/Vite，通过 LLM 对 `.tex` 文件进行智能处理。

## 功能

- **文本校对**：逐段检查拼写错误，定位并修正，保留 LaTeX 结构不变
- **表达优化**：按特定风格（如清晰、简洁、学术语气）优化表达，支持自定义提示词
- **公式识别**：上传公式图片，通过 LLM Vision API 识别为 LaTeX 代码
- **表格识别**：上传表格图片，通过 LLM Vision API 识别为标准 LaTeX 浮动体代码（含三线表、合并单元格等）
- **模板生成**：用自然语言描述需求，自动生成 LaTeX 文档模板
- **源码格式化**：优化 LaTeX 源码的间距、缩进、环境对齐
- **自动编译**：一键调用 latexmk 编译 TeX，错误信息由 LLM 翻译为通俗解释并给出修复建议
- **Markdown 转 LaTeX**：粘贴 Markdown 文本，调用 Pandoc 转换为 LaTeX 代码（需系统安装 Pandoc）

## 技术栈

| 层   | 技术                                                       |
| ---- | ---------------------------------------------------------- |
| 后端 | Python 3.12+, FastAPI, uvicorn, openai, pydantic v2, httpx |
| 前端 | React 19, Vite 6, Tailwind CSS 4, Zustand 5, shadcn/ui     |
| 测试 | pytest + pytest-asyncio                                    |

## 快速开始

### 前置条件

- Python 3.12+
- Node.js 18+
- texlive（含 xelatex 和 latexmk，编译功能需要）
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

# 编辑 config.json，填入你的 API Key
```

环境变量 `OPENAI_API_KEY` 优先级高于配置文件中的 `api_key`。

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
# 构建前端
cd frontend
npm run build
cd ..

# 启动（自动挂载构建产物）
python main.py config.json
```

打开 http://localhost:8000

## 核心工作流

1. 打开本地 LaTeX 项目目录
2. 选择 .tex 文件，系统解析段落
3. LLM 在纯文本上执行校对/优化
    - LaTeX 结构被替换为不可见的 marker，LLM 不能修改它们
4. 用户审核 diff 结果，逐项确认
5. 确认后，系统将 marker 还原为 LaTeX 并原子性写回源文件

## 项目结构

```
latexcellence/
├── main.py                  # 统一入口
├── config.json              # 配置文件
├── backend/
│   ├── core/                # 配置、LLM 客户端、日志、项目管理
│   ├── modules/             # 功能模块（路由 + 服务）
│   │   ├── typo_check/      # 文本校对
│   │   ├── expression_opt/  # 表达优化
│   │   ├── formula_ocr/     # 公式识别
│   │   ├── table_ocr/       # 表格识别
│   │   ├── template_gen/    # 模板生成
│   │   ├── formatting/      # 源码格式化
│   │   ├── compile/         # 自动编译
│   │   └── markdown_converter/  # Markdown 转 LaTeX
│   └── utils/               # 文件操作、JSON 校验
├── frontend/
│   └── src/                 # React 应用
├── prompts/                 # 自定义提示词
├── templates/               # 生成的模板
├── tests/                   # 测试
└── docs/                    # 项目文档
```
