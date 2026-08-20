/* ============================================================
 * LateXcellence · Showcase — 页面内容单一来源
 *
 * 所有文案、场景结构、导航、代码块内容都集中在本文件。
 * 修改页面内容（新增/删除章节、改文案、改 mock）只需改这里，
 * index.html 只保留静态骨架，由 js/render.js 按本数据生成 DOM。
 *
 * 场景模板（tpl）：
 *   hero       序章 · 封面（字标 + slogan + 编辑器/纸面签名装置）
 *   act        第一幕/第二幕（scene-copy + 自定义舞台 stage）
 *   chapter    §1–§6 功能章节（kicker/title/desc + 副卡片 + 主 feature 卡片）
 *   principles 设计理念（三条核心主张）
 *   stack      技术栈（三行横排卡片）
 *   themes     主题（横向滑动色卡，卡片由 js/themes.js 的 THEMES 生成）
 *   outro      尾声
 * ============================================================ */

/* ---------- 右侧章节导轨（与场景 id 一一对应） ---------- */
export const NAV = [
  { id: "scene-hero", no: "序", name: "开场" },
  { id: "scene-import", no: "§1", name: "内容导入" },
  { id: "scene-generate", no: "§2", name: "内容生成" },
  { id: "scene-review", no: "§3", name: "文本审查" },
  { id: "scene-code", no: "§4", name: "代码与排版" },
  { id: "scene-version", no: "§5", name: "版本控制" },
  { id: "scene-ai", no: "§6", name: "AI 辅助" },
  { id: "scene-principles", no: "理", name: "设计理念" },
  { id: "scene-stack", no: "栈", name: "技术栈" },
  { id: "scene-themes", no: "彩", name: "主题" },
];

/* ---------- Hero 打字机内容（slogan.tex） ---------- */
export const TYPE_LINES = [
  "% slogan.tex",
  "\\documentclass{ctexart}",
  "\\begin{document}",
  "让技术退居幕后，让思想落于纸上",
  "\\end{document}",
];

/* ---------- 代码块内容（HTML 字符串，经 [data-code] 注入） ----------
 * 场景数据里通过 { type: "code" | "file-tree" | "terminal", ref } 引用，
 * 只有被引用的 key 才会渲染，未引用的 key 请直接删除。
 * ------------------------------------------------------------ */
export const CODE_BLOCKS = {
  "formula-ocr":
    "\\[\\int_{0}^{\\infty} e^{-x^{2}} dx = \\frac{\\sqrt{\\pi}}{2}\\]",
  "template-file-tree":
    "project/\n" +
    "├─ <b>main.tex</b>\n" +
    "├─ chapters/\n" +
    "│  ├─ intro.tex\n" +
    "│  └─ method.tex\n" +
    "├─ figures/\n" +
    "└─ refs.bib",
  "template-terminal":
    "$ latexmk -xelatex main.tex\n" +
    '<span class="dim">… running xelatex …</span>\n' +
    '<span class="ok">✓ Output written on main.pdf</span>',
};

/* ============================================================
 * 场景数据
 * ============================================================ */
export const SCENES = [
  /* ---------------- 序章 · 封面 ---------------- */
  {
    tpl: "hero",
    id: "scene-hero",
    len: 2.6,
    label: "序章 · 封面",
    glyphs: [
      { cls: "g1", parallax: "-0.16", text: "\\int_0^1 x^2 \\, dx" },
      { cls: "g2", parallax: "-0.3", text: "\\frac{\\partial f}{\\partial x}" },
      { cls: "g3", parallax: "-0.1", text: "\\sum_{n=1}^{\\infty}" },
      { cls: "g4", parallax: "-0.24", text: "\\section{结果与分析}" },
      { cls: "g5", parallax: "-0.08", text: "e^{i\\pi} + 1 = 0" },
      { cls: "g6", parallax: "-0.2", text: "\\begin{tabular}{ccc}" },
    ],
    eyebrow: "AI · LaTeX · Academic Writing",
    wordmark: "LaTeXcellence",
    slogan: "让技术退居幕后，让思想落于纸上",
    lead:
      "我们不追求改变习惯，只致力于解放那些深陷 LaTeX 繁琐之中的灵魂。真正的创作，不应被编译错误与排版细节所打断。" +
      "LateXcellence 用 AI 消除工具的存在感，让“心想”即是“事成”。",
    actions: [
      { href: "#scene-import", label: "开始浏览", icon: "i-arrow" },
      { href: "#scene-principles", label: "设计理念" },
    ],
    meta: ["<b>FastAPI</b> + <b>React</b>", "LLM Agent", "TeX Live 工具链"],
  },

  /* ---------------- 第一幕 · 摩擦 ---------------- */
  {
    tpl: "act",
    cls: "scene-problem",
    stageCls: "problem-stage",
    id: "scene-problem",
    len: 2.8,
    label: "第一幕 · 摩擦",
    glyphs: [
      { cls: "g1", parallax: "0.1", text: "! Emergency stop" },
      { cls: "g2", parallax: "0.18", text: "? h for help" },
      { cls: "g3", parallax: "0.06", text: "Missing $ inserted" },
    ],
    ghost: "00",
    kicker: "Act 1 · Friction",
    title: "LaTeX 不该是写作的绊脚石",
    desc: [
      "公式、表格、引用、编译报错……每一处都在打断思考。",
      "只会在对话框空谈的“AI 助手”，帮不上真正的忙：",
      "它看不到你的文件，也走不进你的工程。",
    ],
    points: [
      "编译报错看不懂，日志像天书",
      "宏包总缺、表格对齐全靠手调",
      "参考文献编译半天，最后还是一串问号",
    ],
    stage: {
      tpl: "friction",
      terminal: {
        bar: "latexmk -xelatex main.tex",
        badge: "编译失败",
        badgeCls: "danger",
        lines: [
          { cls: "dim", text: "$ latexmk -xelatex main.tex" },
          {
            cls: "err",
            text: "! LaTeX Error: File `fig/result.pdf' not found.",
          },
          { cls: "dim", text: "l.64 \\includegraphics{fig/result}" },
          { cls: "err", text: "? Emergency stop." },
          { cls: "dim", text: "… 尝试 4 次 · 用时 92s" },
        ],
      },
      chat: {
        user: "帮我修一下这个编译错误",
        assistant:
          "抱歉，我无法直接修改你的文件。请手动检查 fig/result.pdf 是否存在后重试。",
      },
    },
  },

  /* ---------------- 第二幕 · 解法 ---------------- */
  {
    tpl: "act",
    cls: "scene-answer",
    stageCls: "answer-stage",
    id: "scene-answer",
    len: 2.8,
    label: "第二幕 · 解法",
    glyphs: [
      { cls: "g1", parallax: "-0.1", text: "__MATH_0000__" },
      { cls: "g2", parallax: "-0.18", text: "__CMD_0000__" },
      { cls: "g3", parallax: "-0.06", text: "marker" },
    ],
    ghost: "✓",
    kicker: "Act 2 · The Fix",
    title: "让 Agent 在你的工程里完成闭环",
    desc: [
      "描述目标后，Agent 会规划步骤、修改文件、运行编译并读取日志。",
      "遇到错误，它继续修复并验证结果，直到交付可用产物。",
    ],
    points: [
      "工具调用真正读写文件、编译验证",
      "LaTeX 结构替换为不可见 marker，LLM 只改纯文本",
      "写回前经 JSON 校验、内容校验、MD5 乐观锁与原子写入",
    ],
    stage: {
      tpl: "answer",
      marker: {
        bar: "marker 保护 · 还原",
        badge: "结构零改动",
        badgeCls: "ok",
        beforeHtml:
          '<span class="c-kw">\\begin</span>{align*}\n' +
          '  a^2 + b^2 &amp;= c^2 <span class="c-cm">\\\\</span>\n' +
          "  E &amp;= mc^2\n" +
          '<span class="c-kw">\\end</span>{align*}',
        afterText: "__MATH_0000__",
        note: "LLM 只看到 <code>__MATH_0000__</code>，永远碰不到公式结构。",
      },
      loop: [
        "解析需求",
        "写入文件",
        "latexmk 编译",
        "读日志 · 修错 · 再编译",
        "输出 main.pdf",
      ],
    },
  },

  /* ---------------- §1 内容导入 ---------------- */
  {
    tpl: "chapter",
    id: "scene-import",
    len: 3.2,
    label: "§1 内容导入",
    ghost: "§1",
    kicker: "§1 · Content Import",
    title: "内容导入",
    desc: [
      "把图片与 Markdown 请进 LaTeX 的世界：",
      "公式、表格，一键化作可编辑的源码。",
    ],
    glyphs: [
      {
        cls: "g1",
        parallax: "-0.12",
        text: "\\int_{0}^{\\infty} e^{-x^{2}} dx",
      },
      { cls: "g2", parallax: "-0.2", text: "\\begin{tabular}{lll}" },
    ],
    subCards: [
      {
        id: "feature-table-ocr",
        icon: "i-table",
        title: "表格 OCR 识别",
        desc: "从图片识别表格结构，生成可编译的 tabular，并保留列对齐与合并单元格。",
      },
      {
        id: "feature-format-convert",
        icon: "i-swap",
        title: "格式转换",
        desc: "在 Markdown 与 LaTeX 之间转换，并根据内容分析所需宏包。",
      },
    ],
    feature: {
      id: "feature-formula-ocr",
      icon: "i-scan",
      title: "公式 OCR 识别",
      tag: "formula_ocr",
      desc: "上传公式截图，识别为 LaTeX 代码；系统自动判断行内或行间格式，复制后即可继续编辑。",
      mock: {
        bar: "formula_ocr",
        badge: "行间公式",
        badgeCls: "blue",
        body: [
          {
            type: "col",
            items: [
              { type: "formula-ocr" },
              { type: "code", ref: "formula-ocr" },
            ],
          },
        ],
      },
    },
  },

  /* ---------------- §2 内容生成 ---------------- */
  {
    tpl: "chapter",
    id: "scene-generate",
    len: 3.2,
    label: "§2 内容生成",
    ghost: "§2",
    kicker: "§2 · Content Generation",
    title: "内容生成",
    desc: "从一句需求生成可编译工程，从数据生成矢量图，从 DOI 整理参考文献。",
    glyphs: [
      { cls: "g1", parallax: "-0.12", text: "\\documentclass{ctexart}" },
      { cls: "g2", parallax: "-0.2", text: "\\bibliography{refs}" },
    ],
    subCards: [
      {
        id: "feature-plot-gen",
        icon: "i-chart",
        title: "科研绘图",
        desc: "分析数据并推荐图表，隔离执行 Python 脚本，输出 PDF、SVG 或 TikZ。",
      },
      {
        id: "feature-bib-manager",
        icon: "i-book",
        title: "参考文献管理",
        desc: "从多个数据源查询 DOI，识别重复文献，并生成规范 BibTeX 条目。",
      },
    ],
    feature: {
      id: "feature-template-gen",
      icon: "i-scroll",
      title: "模板生成",
      tag: "template_gen",
      desc: "输入排版要求，生成多文件工程，并逐步写入、编译、读日志、修错，交付通过构建的项目。",
      mock: {
        bar: "template_gen · Agent 轨迹",
        badge: "编译通过",
        badgeCls: "ok",
        body: [
          {
            type: "steps",
            items: [
              "解析需求：硕士论文 · 理工科模板",
              "规划多文件结构",
              "写入 main.tex / chapters/ / refs.bib",
              "latexmk 编译验证 → 成功",
            ],
          },
          {
            type: "row",
            style: "align-items: stretch",
            items: [
              {
                type: "file-tree",
                ref: "template-file-tree",
                style: "flex: 1; min-width: 200px",
              },
              {
                type: "terminal",
                ref: "template-terminal",
                style: "flex: 1; min-width: 200px",
              },
            ],
          },
        ],
      },
    },
  },

  /* ---------------- §3 文本审查与优化 ---------------- */
  {
    tpl: "chapter",
    id: "scene-review",
    len: 3.2,
    label: "§3 文本审查与优化",
    ghost: "§3",
    kicker: "§3 · Text Review",
    title: "文本审查与优化",
    desc: "按段落发现问题、查看建议、确认修改。LaTeX 结构由 marker 保护，LLM 只处理纯文本。",
    glyphs: [
      { cls: "g1", parallax: "-0.12", text: "\\subsection{实验设置}" },
      { cls: "g2", parallax: "-0.2", text: "[已修复]" },
    ],
    subCards: [
      {
        id: "feature-expression-opt",
        icon: "i-pencil",
        title: "表达优化",
        desc: "明确句意、收紧表达、调整学术语气，风格也可由你定义。",
      },
      {
        id: "feature-terminology-check",
        icon: "i-list",
        title: "术语一致性检查",
        desc: "提取全文术语并按语义归类，检查大小写、译法和缩写是否统一。",
      },
      {
        id: "feature-paragraph-translate",
        icon: "i-lang",
        title: "逐段翻译",
        desc: "按段落完成中英互译，保留 LaTeX 结构，审阅后再应用译文。",
      },
    ],
    feature: {
      id: "feature-typo-check",
      icon: "i-spell",
      title: "文本校对",
      tag: "typo_check",
      desc: "逐段标出错别字并给出修改建议。你可以逐项确认、批量应用，也可以随时撤回。",
      mock: {
        bar: "typo_check",
        badge: "段落 3 · 已修复",
        badgeCls: "",
        body: [
          {
            type: "diff-del",
            html: "实验表明，该算法在训练过程<b>总</b>表现稳定，收敛速度较快。",
          },
          {
            type: "diff-add",
            html: "实验表明，该算法在训练过程<b>中</b>表现稳定，收敛速度较快。",
          },
        ],
      },
    },
  },

  /* ---------------- §4 代码审查与排版 ---------------- */
  {
    tpl: "chapter",
    id: "scene-code",
    len: 3.2,
    label: "§4 代码审查与排版",
    ghost: "§4",
    kicker: "§4 · Code Review & Formatting",
    title: "代码审查与排版",
    desc: "让代码结构更清楚，让样式修改更直接。先格式化、再审查，所有改动都可查看和回退。",
    glyphs: [
      { cls: "g1", parallax: "-0.12", text: "latexindent -w main.tex" },
      { cls: "g2", parallax: "-0.2", text: "\\color{primary}" },
    ],
    subCards: [
      {
        id: "feature-code-review",
        icon: "i-list",
        title: "LaTeX 代码审查",
        desc: "先经 latexindent 格式化，再由 LLM 给出只读建议，不自动改写文件。",
      },
    ],
    feature: {
      id: "feature-style-modify",
      icon: "i-ruler",
      title: "LaTeX 样式修改",
      tag: "style_modify",
      desc: "描述描述想要的样式，后端便定位代码、精准改写。",
      mock: {
        bar: "style_modify",
        badge: "“章节标题改为主题蓝色”",
        badgeCls: "warn",
        body: [
          { type: "diff-del", html: "\\section{引言}" },
          { type: "diff-add", html: "{\\color{primary}\\section{引言}}" },
          { type: "badges", items: [{ text: "语法校验通过", cls: "ok" }] },
        ],
      },
    },
  },

  /* ---------------- §5 版本控制 ---------------- */
  {
    tpl: "chapter",
    id: "scene-version",
    len: 3.2,
    label: "§5 版本控制",
    ghost: "§5",
    kicker: "§5 · Version Control",
    title: "版本控制",
    desc: "纯文本与 Git 天生相契：审阅历史 diff，编译差异 PDF；AI 的每次改动都可回退，写得再远也有归途。",
    glyphs: [
      { cls: "g1", parallax: "-0.12", text: "git diff HEAD~2" },
      { cls: "g2", parallax: "-0.2", text: "latexdiff" },
    ],
    subCards: [],
    feature: {
      id: "feature-git-diff",
      icon: "i-diff",
      title: "Git 差异高亮",
      tag: "git_diff",
      desc: "选择两个 Git 版本，用 latexdiff 高亮文本变化，并编译一份差异 PDF 供审阅。",
      mock: {
        bar: "git_diff · HEAD~2 → HEAD",
        badge: "PDF 已生成",
        badgeCls: "ok",
        body: [
          { type: "diff-hunk", text: "@@ -12,7 +12,7 @@ 实验设置" },
          { type: "diff-del", html: "- 实验表明，模型表现良好。" },
          {
            type: "diff-add",
            html: "+ 实验表明，所提模型性能显著优于基线模型。",
          },
        ],
      },
    },
  },

  /* ---------------- §6 AI 辅助 ---------------- */
  {
    tpl: "chapter",
    id: "scene-ai",
    len: 3.2,
    label: "§6 AI 辅助",
    ghost: "§6",
    kicker: "§6 · AI Assistance",
    title: "AI 辅助",
    desc: "让写作助手理解你的项目上下文，帮你检索文献、追踪引文和修改段落。每次操作都清晰可见。",
    glyphs: [
      { cls: "g1", parallax: "-0.12", text: "web_search" },
      { cls: "g2", parallax: "-0.2", text: "openalex" },
    ],
    subCards: [],
    feature: {
      id: "feature-writing-chat",
      icon: "i-chat",
      title: "写作助手",
      tag: "writing_chat",
      desc: "记得上下文的对话：联网搜索、检索文献、追踪引文、翻阅项目、修改段落，皆在一处。",
      mock: {
        bar: "writing_chat",
        badge: "深度思考",
        badgeCls: "",
        body: [
          {
            type: "chat",
            user: "帮我润色讨论部分的结论句，并补充 2–3 篇近年相关文献。",
            think: [
              "· 正在分析段落论证结构与上下文…",
              "· 计划调用 web_search 检索 2023–2026 相关文献",
            ],
            tools: ["web_search", "openalex", "edit_paragraph"],
            assistant:
              "已定位 3 篇可引文献，并将结论句改写为更明确的主张。以下是编辑建议：",
            add: "该方法的有效性已在多个公开数据集上得到验证，且与近期工作方向一致。",
          },
        ],
      },
    },
  },

  /* ---------------- 设计理念 ---------------- */
  {
    tpl: "principles",
    id: "scene-principles",
    len: "3.0",
    label: "设计理念",
    glyphs: [],
    head: {
      kicker: "Design Philosophy",
      title: "三条核心主张",
      desc: "LateXcellence 的可靠，长在架构的约束里。",
    },
    cards: [
      {
        icon: "i-bot",
        title: "会自主干活",
        desc: "写作助手、模板生成、科研绘图、LaTeX 样式修改，都是真正的 Agent：规划、调工具、执行、验证、出了错自己修，每一步都有迹可循。",
        points: [
          "工具调用真正读写文件、编译验证",
          "Function Calling 工具注册表统一管理",
          "每一步执行都有 trace 可回放",
        ],
      },
      {
        icon: "i-shield",
        title: "修改始终可控",
        desc: "marker 保护 LaTeX 结构，LLM 只修改纯文本；写回前经过内容校验、乐观锁与原子写入。",
        points: [
          "结构替换为不可见 marker，按长度降序还原",
          "MD5 乐观锁检测多端并发冲突",
          "原子写入 + 自动备份，源文件安全",
        ],
      },
      {
        icon: "i-gauge",
        title: "可靠可验证",
        desc: "结构化校验和重试机制检查模型输出；内置人工评测，让效果有数据为证。",
        points: [
          "JSON 容错解析 + Pydantic 严格校验 + 重试",
          "LaTeX 内容校验：括号配平、转义防护",
          "内置评测可产出准确率 / 成功率报告",
        ],
      },
    ],
  },

  /* ---------------- 技术栈 ---------------- */
  {
    tpl: "stack",
    id: "scene-stack",
    len: 2.4,
    label: "技术栈",
    glyphs: [
      { cls: "g1", parallax: "-0.1", text: "Python 3.12" },
      { cls: "g2", parallax: "-0.22", text: "React 19" },
      { cls: "g3", parallax: "-0.05", text: "TeX Live" },
    ],
    head: {
      kicker: "Tech Stack",
      title: "技术栈",
      desc: "FastAPI 与 React 携手，围绕 LaTeX 工具链与 LLM 精雕细琢。",
    },
    cards: [
      {
        icon: "i-terminal",
        name: "后端",
        tags: [
          "Python 3.12+",
          "FastAPI",
          "uvicorn",
          "openai",
          "Pydantic v2",
          "httpx",
          "pytest",
        ],
        desc: "多工作区隔离、乐观锁、marker 解析、宏包规则表、TeX 编译封装、Web 搜索与学术检索引擎。",
      },
      {
        icon: "i-palette",
        name: "前端",
        tags: [
          "React 19",
          "Vite 6",
          "Tailwind CSS 4",
          "Zustand 5",
          "shadcn/ui",
          "KaTeX",
          "highlight.js",
        ],
        desc: "切片式状态管理、七套主题 × 明暗双档、代码高亮统一收敛到 CodeBlock / MarkerText，页面文字中英双语。",
      },
      {
        icon: "i-zap",
        name: "工具链",
        tags: [
          "TeX Live",
          "latexmk",
          "latexindent",
          "latexdiff",
          "Pandoc",
          "Git",
        ],
        desc: "编译、格式化、差异对比、格式转换全部由真实工具链完成，LLM 负责解释与生成。",
      },
    ],
  },

  /* ---------------- 主题 · 七种气质 ---------------- */
  {
    tpl: "themes",
    id: "scene-themes",
    len: "3.0",
    label: "主题",
    glyphs: [],
    head: {
      kicker: "Themes",
      title: "一套设计，七种气质",
      desc: "轻点一张色卡，整页便换了气质。",
      hint: "继续滚动，色卡横向滑过",
    },
  },

  /* ---------------- 尾声 ---------------- */
  {
    tpl: "outro",
    id: "scene-outro",
    len: 1.8,
    label: "尾声",
    glyphs: [{ cls: "g1", parallax: "-0.15", text: "\\end{document}" }],
    eyebrow: "Compiled.",
    title: "让技术退居幕后<br>让思想落于纸上",
    desc: "从内容导入到 AI 辅助，从文本校对到版本控制。此刻，打开你的 .tex 工程，把形式的琐碎交给 AI，把时间还给思考。",
    actions: [
      { href: "#scene-import", label: "重新浏览", icon: "i-arrow" },
      { href: "#", label: "回到顶部", icon: "i-up", id: "toTopBtn" },
    ],
  },
];
