# template_gen 基准项目特征提取报告

> 用途：作为 `template_gen` Agent 系统提示词与约束设计的"工业级质量"参照系。
> 依据：诸多优秀模板的 README 与关键源码。

## 1. 共性架构特征（三类项目全部成立）

### 1.1 格式与内容彻底分离

- 样式全部封装在 `.cls` / `.sty` 中，正文 `.tex` 只写语义命令与内容，不允许正文出现裸排版命令（如 `\fontsize`、`\hspace` 调间距、`\definecolor`）。
- 元数据独立成文件：论文模板使用 `thesis-info.tex`（标题/作者/年份/学位），简历模板在入口文件顶部集中填写个人信息，章节型文档使用 `main.tex` + 各章节 `\input`。
- 用户可配置项全部走"选项 / 配置文件"，不硬编码：提供 `print/online`、页边距、字体族、参考文献风格、页面样式、`draft` 等选项；主题色通过 `\colorlet` 或集中变量一处切换。

### 1.2 语义化命令体系

- 简历类通常定义 `\name`、`\position`、`\profilepic`、`\email`、`\phone`、`\homepage`、`\github`、`\linkedin`、`\quote`、`\cvsection`、`\cvsubsection`、`\cventry`、`\cvitem`、`\cvskill`、`\cvtag`，并为每类元素提供样式钩子（如姓名样式、条目标题样式、分节样式），做到样式与语义一一对应。
- 学位论文类通常定义 `\cover`、`\titlepage`、`\declaration`、`\abstract`、`\keywords`、`\advisor`、`\nomenclature` 等语义命令，并采用 `\frontmatter/\mainmatter/\backmatter` 三段结构。
- 结论：**自定义命令是模板的灵魂**，Agent 必须主动设计"文档级语义命令 + 元素级样式钩子"两层体系。

### 1.3 模块化文件组织 + 文档

- 目录按 `front/`（前置）、`chapters/`（正文）、`back/`（后置）、`figures/`（图）、`refs.bib` 组织。
- 都附带 README / 使用说明文档（常见形式包括 PDF 手册或 Markdown 指南）。
- 编译脚本（Makefile / compile-thesis.sh / bat）明确编译链路。

### 1.4 标准宏包优先，不重复造轮子

| 用途 | 标准选择 |
| --- | --- |
| 页面布局 | `geometry` |
| 页眉页脚 | `fancyhdr`（可通过 `\headrulewidth=0pt` 实现极简风） |
| 表格 | `booktabs`（三线表）、`tabularx`/`array`、`longtable`、`threeparttable` |
| 列表 | `enumitem`（紧凑间距 `nosep`） |
| 引用 | `natbib` / `biblatex`（国标 `gbt7714`） |
| 目录/书签 | `hyperref`（`hidelinks,unicode`）+ `bookmark`（解决 out 文件变化警告） |
| 数学 | `amsmath/amssymb/amsthm`；XeLaTeX 下 `unicode-math` |
| 颜色/字体 | `xcolor` + `fontspec`（XeLaTeX）、`ifxetex` 分支 |
| 浮动/图表 | `caption`/`subcaption`、`float`、`graphicx` |
| 章节标题 | `titlesec`（用于定制章标题层级） |
| 行距 | `setspace`（`\onehalfspacing`） |
| 对齐 | `ragged2e`（学位论文推荐 flush-left，照顾阅读障碍） |
| 图标 | `fontawesome6`（适用于联系方式图标化） |
| 中文 | `ctex` / `xeCJK`（中文论文常用） |

## 2. 学位论文基准

### 2.1 宏包与字体

- 中文模板固定走 XeLaTeX + `ctex`/`xeCJK`，宋体正文、黑体标题、Times New Roman 英文/数字；字号层级严格对应国标（正文小四 12pt、章标题黑体三号居中、节黑体四号、小节黑体小四）。
- 常见高质量模板会提供 `times`（Times + 数学支持）、`fourier`、Latin Modern 等多档字体选择，并支持 XeLaTeX 系统字体。
- 行距 `\onehalfspacing`（1.5 倍）是学位论文标准。

### 2.2 布局设计

- 边距由学校规范决定且必须可配置（例如上 3cm 下 2.5cm 左 3cm 右 2.5cm，或通过 `custommargin` 选项在 preamble 用 geometry 指定）。
- 页眉页脚：奇数页章名、偶数页节名或学校名，页码位置规范；`draft` 模式可带水印/行号/时间戳。
- 编号体系：图表 "图 3-1"，公式编号右对齐，目录 `tocdepth`/`secnumdepth` 分级。
- 前置部分：封面 → 原创性声明 → 中英文摘要+关键词 → 目录 → 符号表；后置：致谢 → 发表论文 → 附录。
- 图表：三线表（booktabs）、双语题注（bicaption）、长表格标题悬挂缩进。

### 2.3 视觉美学

- 学院派美学：克制、零装饰、层级靠"字体族对比 + 字号层级 + 行距"建立，不用彩色。
- 一致性：全文档一套字号/间距/编号规则；目录、页眉、正文之间节奏统一。

## 3. 演示文稿基准

### 3.1 海报型与学术展示型 Beamer 主题

- 设计目标：**Minimal（内容优先）/ Batteries included（开箱即用）/ Easy theming（易换主题）**。
- 实现：主题入口与配色文件分离（theme + colortheme），内置多套色板；字体常用无衬线组合（如 Raleway/Lato 一类）。

### 3.2 现代扁平化 Beamer 主题

- 文件结构：主题总入口 + 统一选项接口（如 `titleformat`/`numbering`/`progressbar`/`sectionpage`/`block`）+ color/font/inner/outer 子主题。
- 视觉特征：
  - 扁平化：无阴影、无圆角、无导航装饰，最大化内容空间；唯一视觉点缀是可选**进度条**（帧标题下或页脚）。
  - 标题页用整块背景色块 + 白色标题；帧标题下方有细分割线（rule）。
  - 分节页（section page）展示当前节标题，可带进度条。
  - 页脚：作者 / 机构 / 页码，`numbering=fraction` 显示 "3/12"。
  - 字体：Fira Sans（文本）+ Fira Math（数学），无衬线现代感。
  - block 环境用浅色填充区分 normal/alert/example，alert 用主题强调色。
- 设计理念：**"minimize noise and maximize space for content"**——只保留对导航有意义的元素。

## 4. 简历基准

### 4.1 LaTeX 简历模板（源码级核对）

- **类结构**：基于 article 的简历类文件，`\DeclareOption*` 透传选项；`draft` 时开 `\overfullrule` 暴露排版溢出。
- **宏包清单**（从源码摘录）：`array`、`tabularx`、`enumitem`、`ragged2e`、`geometry`、`fancyhdr`、`xcolor`、`ifxetex`、`xifthen`、`xstring`、`etoolbox`、`setspace`、`fontspec`（HarfBuzz）、`unicode-math`、`fontawesome6`、`accsupp`、`tcolorbox[skins]`、`parskip`、`hyperref[hidelinks,unicode]`、`bookmark`。
- **字体**：Source Sans 3（正文/无衬线）+ Roboto（头部），通过 `FontFace={l}{n}{Font=* Light}` 定义 Light 字重——"标题用细体、正文用常规体"的现代层级。
- **版式**：`geometry{left=2cm,top=1.5cm,right=2cm,bottom=2cm,footskip=.5cm}`；`fancyhdr` 页眉线宽 0、页脚 8pt 小字。
- **字号层级（信息密度核心）**：姓名 32pt、分节 16pt、小节 12pt、条目标题 10pt、正文 9pt、辅助信息 7–8pt；条目时间用右对齐斜体，职位用 small-caps。
- **配色**：灰阶（333/5D5D5D/999）+ 一组强调色（如 emerald/skyblue/red/pink/orange 等），通过 `\colorlet` 集中换主题。
- **语义命令**：`\cventry{年份}{职位}{机构}{地点}{描述}` 等，描述用紧凑列表；技能用 tag 样式（`\cvtag`）；联系方式全部用 fontawesome 图标。
- **细节**：`\setbool{acvSectionColorHighlight}{true}` 控制"分节标题前 3 字符主题色高亮"；`parskip` 段间距代替首行缩进；photo 用 tcolorbox 圆角裁剪。

### 4.2 非 LaTeX 简历工具的可迁移准则

- 非 LaTeX，但排版准则可移植：网格化布局、清晰视觉层级、单一强调色、图标化联系方式、打印 A4 友好、留白与信息密度平衡、无衬线正文。

## 5. 提炼为 Agent 可执行的"工业级模板"检查清单

### 5.1 需求映射（解决"不遵循用户输入要求"）

每条用户要求必须有**一一对应的 LaTeX 实现**，例如：

| 用户要求 | 必须实现为 |
| --- | --- |
| 边距/纸张 | `geometry` 明确数值，禁默认边距 |
| 字体/字号/行距 | `fontspec`/`ctex` 设定 + 显式字号命令 |
| 主题色 | 集中 `\definecolor` + `\colorlet`，样式全部引用颜色变量 |
| 章节编号样式 | `titlesec`/ctexset 定制，如"第 N 章" |
| 三线表 | `booktabs` 的 `\toprule/\midrule/\bottomrule` |
| 引用格式 | `natbib`/`biblatex` + 对应样式 |
| 现代 beamer 风格 | 独立 theme sty，扁平化、进度条、色块标题页 |
| 简历信息密度 | 紧凑边距 + 7–12pt 字号层级 + 语义条目命令 |

### 5.2 代码可维护性检查清单

- [ ] 样式封装在 `.cls`/`.sty`，正文零裸排版命令
- [ ] 元数据与正文分离（settings/thesis-info 文件）
- [ ] 语义命令 + 样式钩子两层体系
- [ ] 主题色/字体集中定义，一处可换
- [ ] 只用标准宏包，无重复加载、无自定义"土办法"
- [ ] 文件按 front/chapters/back 组织，`\input` 拆分
- [ ] README 说明编译方式（引擎/文献命令/依赖）
- [ ] 类选项或主题选项暴露可配置项
- [ ] 示例内容覆盖：公式、图表、表格、引用、目录、脚注
- [ ] 无 `Overfull \hbox` 溢出、无 `undefined reference` 警告

### 5.3 视觉美学检查清单

- [ ] 字号/字重/间距构成明确层级（大标题/标题/正文/辅助四档起步）
- [ ] 留白均匀，版心比例合理，无文字贴边
- [ ] 配色克制（≤3 个强调色 + 灰阶），对比度达标
- [ ] 对齐体系统一（左对齐/居中/右对齐各有用途）
- [ ] 装饰有目的（进度条、分割线、图标），无随机花哨
- [ ] 信息密度与可读性平衡（简历紧凑、论文规范、幻灯片留白多）

## 6. tjuthesis 专项基准（复刻目标）

来源：<https://github.com/HaimingZ/tjuthesis>（天津大学硕博学位论文模板，
依据《天津大学关于博士、硕士学位论文统一格式的规定》）。该模板以"单 `.cls` +
`main.tex` 编排 + `contents/` 分文件"的极简架构达到存档级质量，是
`tju` 评测场景的复刻基准。

### 6.1 类与选项

- `\LoadClass[openright, twoside, a4paper]{book}`；自定义选项 `master` /
  `phd`（决定偶数页页眉文案）/ `print`（切换装订侧边距），`\DeclareOption` +
  `\ProcessOptions*` 实现。
- 页边距：`top=27.5mm, bottom=25.4mm`；电子版 `left=right=31.7mm`，
  打印版 `left=35.7mm, right=27.7mm`；再以 `headsep=7.5mm,
  headheight=5mm`（页眉距边界 15mm）、`footskip=7.9mm`（页脚距边界 17.5mm）
  精确落地。

### 6.2 中文字体与标题体系

- `ctex[heading=true, zihao=-4]` 开启中文章节/图表标题、默认正文小四；
  `\setmainfont{Times New Roman}`；宋体经 `\setCJKfamilyfont{zhsong}
  [AutoFakeBold=3.17]{SimSun}` 提供伪粗体；另设 GB2312 楷体（`kaigb`）
  专供声明页学校名称。
- `\ctexset` 逐级定制：章=黑体小三居中、编号为阿拉伯数字、`aftername=~`，
  二/三级=黑体四号，四级=黑体小四，并为每级设置 `beforeskip/afterskip`
  精确间距；`secnumdepth=3`。
- 正文行距固定 20bp（`\setlength{\baselineskip}{20bp plus 2bp minus 2bp}`），
  脚注字号 `10.5bp/13bp`（五号）。

### 6.3 页面结构与编号

- 重定义 `\frontmatter`（`\cleardoublepage` + `noheader` 样式 +
  `\pagenumbering{Roman}`）与 `\mainmatter`（`plain` 样式 +
  `\pagenumbering{arabic}`），实现"摘要罗马页码、正文从 1 重排"。
- `fancyhdr` 两个样式：`plain`（奇数页 `\leftmark` 章名、偶数页"天津大学
  硕士学位论文/博士学位论文"、页脚居中页码、`\headrulewidth=0.5bp`）、
  `noheader`（仅页脚页码）。
- 图表公式编号 `\arabic{chapter}-\arabic{...}`；`caption` 图下/表上、
  `font=small`、`labelsep=space`；`enumitem[shortlabels]` 的 `nosep`
  紧凑列表；目录 `tocdepth=2` + `titletoc` 点引导线。

### 6.4 语义命令与参考文献

- 自定义引用命令：`\figref`（图~...）、`\tabref`（表~...）、
  `\eqref`（式~(...)）、`\chref`（第~...~章）、`\secref`（第~...~节）。
- `biblatex[backend=biber, natbib=true, style=gb7714-2005,
  gbnamefmt=quanpin, gbmedium=false, doi=false]`；`main.tex` 用
  `\addbibresource{references.bib}` + `\printbibliography[heading=bibintoc]`。
  注意：若编译沙箱未安装 biblatex-gb7714（如本项目 TeX Live 环境），
  用 natbib + `gbt7714`（`\usepackage[super]{gbt7714}` +
  `\bibliographystyle{gbt7714-2005-numerical}`）实现等价的 2005 上标引用。
- 主文件流程：扉页 → 声明 → `\frontmatter`（摘要、目录）→ `\mainmatter`
  （正文分章）→ 参考文献 → 附录 → `\backmatter`（发表论文情况、致谢）。

### 6.5 与通用论文模板的差异点（Agent 易漏）

- 封面页信息表（一级学科/研究方向/作者/导师）与答辩委员会名单表，而非
  通用 `\maketitle`；
- 声明页"独创性声明 + 版权使用授权书"双页结构，保留签名/日期下划线；
- 摘要标题用 `\chapter*{摘 \qquad 要}` 与 `ABSTRACT`，关键词带
  `关键词：` / `KEY WORDS:` 前缀；
- 偶数页页眉文案随 `master` / `phd` 选项变化，不是写死；
- 数字编号"第 1 章"由 `\arabic{chapter}` + `aftername=~` 组合实现，
  而非硬编码中文数字。

---

*本文档是 template_gen Agent 提示词工程的输入，不替代项目正式文档。*
