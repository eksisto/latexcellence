# -*- coding: utf-8 -*-
"""template_gen 优化闭环的评测场景（模拟用户口吻的自然语言描述）。

每个场景都埋入了可客观核验的硬性要求（边距数值、字体、主题色、
文件结构、语义命令），用于检验 Agent 是否"遵循用户输入要求"。

REQUIREMENT_PATTERNS 是 run_templategen.py 实际使用的正则检查表；
REQUIREMENT_CHECKS 由检查表标签自动派生，供人工复核参考，避免双份维护。
"""

SCENARIOS = {
    # ── 学位论文 ──────────────────────────────────────────────────────
    "thesis": (
        "帮我生成一个硕士学位论文的 LaTeX 模板，中文为主、英文摘要在后。\n"
        "格式要求：页面 A4，上下边距 2.5cm，左右边距 3cm；正文小四号（12pt）、"
        "1.5 倍行距，中文字体用宋体、标题用黑体，英文和数字用 Times New Roman。\n"
        "章节标题居中显示，编号格式是“第1章”“1.1”这种中文风格；"
        "图表标题用三线表（booktabs），图片和表格要有交叉引用示例，公式要编号且右对齐。\n"
        "结构上必须有：封面、原创性声明、中英文摘要与关键词、目录、符号表、"
        "正文（绪论、相关工作、研究方法、实验结果、结论五章）、参考文献（GB/T 7714 国标样式）、"
        "致谢、附录、个人简历。参考文献用 BibTeX 管理，提供一个 .bib 示例。\n"
        "页眉左侧放学校名称、右侧放当前章名，页脚中间放页码；"
        "目录、图表目录都要有。\n"
        "工程组织要求：所有格式定义封装到一个 .cls 或 .sty 文件里，"
        "正文按章节拆分到 chapters/ 目录，封面和声明等放到 front/，"
        "并提供 README 说明编译方式（必须用 XeLaTeX）。\n"
        "内容用占位示例文本即可，但结构和格式必须完整、规范。"
    ),
    # ── 演示文稿 ──────────────────────────────────────────────────────
    "beamer": (
        "帮我做一个现代风格的学术报告幻灯片模板，用 beamer。\n"
        "风格要接近 metropolis：扁平化设计、不要导航条和阴影圆角，"
        "内容区尽量大；标题页做成整块背景色块配白色大字；"
        "帧标题下方加一条细分割线；页面底部显示页码，用“当前页/总页数”的形式；"
        "加一个进度条表示演讲进度。\n"
        "主题色用深蓝色（#00539F），强调色用橙色（#F5A623），"
        "所有颜色集中定义在独立的 color theme 文件里，方便整体换主题。\n"
        "字体用无衬线字体（Fira Sans 优先，没有就用默认 sans 字体），"
        "数学公式也要配套的字体。\n"
        "结构要求：要有 \\section 分节页、block 环境示例（含 alert 和 example）、"
        "两栏布局、图片排版示例、表格示例、列表示例；"
        "主题封装成独立的 .sty 文件（theme/color/font 分文件或集中一个都行），"
        "正文按 section 拆成多个 .tex 文件用 \\input 引入，附 README。\n"
        "内容用真实的学术示例（比如一个机器学习课题的开题汇报），"
        "视觉上要有冲击力，不要默认 beamer 那种老气样子。"
    ),
    # ── 简历 ──────────────────────────────────────────────────────────
    "resume": (
        "帮我生成一个信息密度高、排版精致的单页求职简历模板。\n"
        "页面 A4，边距要紧凑：上下 1.5cm、左右 2cm；正文 9-10pt 的无衬线字体"
        "（Source Sans 或 Roboto 风格），行距要紧凑但不拥挤。\n"
        "头部：姓名用大字号（28pt 以上），姓氏和名字要有粗细对比，"
        "下面一行是求职职位（小型大写、主题色），再下面一行用 fontawesome 图标"
        "排邮箱、电话、主页、GitHub 等联系方式。\n"
        "分节标题（教育背景、工作经历、项目经历、技能、荣誉奖项）用加粗大标题，"
        "标题前三个字符用主题色高亮；每个工作/项目条目里，"
        "时间范围右对齐、职位斜体、地点紧随其后，要点用紧凑列表。\n"
        "技能部分用 tag 样式展示关键词（比如 LaTeX、Python、PyTorch）。\n"
        "主题色要集中定义（一个 \\definecolor 或 \\colorlet 换主题），"
        "默认用深蓝或红黑搭配。\n"
        "工程要求：所有版式封装到一个 .cls 文件，正文只调用 \\name、\\position、"
        "\\cventry、\\cvsection 这类语义命令，附 README；"
        "示例内容写一份真实的算法工程师简历，单页装下。"
    ),
    # ── 需求遵守压力测试（混合要求） ─────────────────────────────────
    "requirements": (
        "生成一个学术会议论文模板，要求：\n"
        "- 纸张 Letter，四周边距 1 英寸\n"
        "- 双栏排版（multicol 或 IEEEtran 风格），正文 10pt\n"
        "- 标题页：论文标题用 16pt 加粗居中，作者两列排列，机构与邮箱用小字号\n"
        "- 摘要用粗体“Abstract”字样开头，关键词用斜体“Keywords:”前缀\n"
        "- 定理、定义、例子三个环境要编号且共享一套编号（同一个 counter）\n"
        "- 图表用三线表，公式编号格式 (1)、(2)，引用时用 \\eqref\n"
        "- 参考文献用 natbib，author-year 风格\n"
        "- 主题色：标题和链接用深蓝色 #1F4E79\n"
        "- 所有样式封装在 .sty 文件，主文件只调用语义命令，附 README\n"
        "- 正文提供 2-3 节示例内容，包含公式、表格、图片占位、引用示例"
    ),
}

CATEGORY_LABELS = {
    "thesis": "学位论文",
    "beamer": "演示文稿",
    "resume": "简历",
    "requirements": "需求遵守",
}

REQUIREMENT_PATTERNS = {
    "thesis": [
        (
            "a4paper 或 geometry 中 2.5cm/3cm 边距",
            [
                r"a4paper",
                r"left\s*=\s*3cm",
                r"right\s*=\s*3cm",
                r"(?:top|bottom)\s*=\s*2\.5cm",
            ],
        ),
        (
            "中文（ctex/xeCJK）与宋体/黑体设置",
            [r"ctex", r"xeCJK", r"SimSun|宋体|Songti", r"SimHei|黑体"],
        ),
        ("1.5 倍行距", [r"onehalfspacing", r"setstretch\{1\.5", r"linespread\{1\.5"]),
        (
            "frontmatter/mainmatter/backmatter 或对应前置/正文/后置结构",
            [
                r"frontmatter|mainmatter|backmatter",
                r"maketitlepage|declarationpage|abstractpage",
                r"input\{front|input\{chapters",
            ],
        ),
        ("中英文摘要与关键词", [r"abstract", r"keywords", r"摘要", r"关键词"]),
        ("booktabs 三线表", [r"booktabs", r"toprule", r"midrule", r"bottomrule"]),
        (
            "参考文献（gbt7714 / bibtex / biblatex）",
            [r"gbt7714", r"bibtex", r"biblatex", r"bibliography", r"addbibresource"],
        ),
        ("页眉页脚（学校名 + 章名 + 页码）", [r"fancyhdr", r"headrule", r"pagestyle"]),
        ("XeLaTeX 编译说明", [r"xelatex", r"XeLaTeX"]),
        (
            ".cls/.sty 封装 + chapters/ 拆分 + README",
            [r"\.cls|\.sty", r"chapters/", r"README"],
        ),
    ],
    "beamer": [
        (
            "beamer + 自定义 theme sty",
            [r"documentclass[^\n]*beamer", r"usetheme", r"\.sty"],
        ),
        (
            "扁平化（无导航/无圆角阴影装饰）",
            [
                r"navigationsymbols",
                r"setbeamertemplate\{navigation symbols\}",
                r"no shadow|no rounded",
            ],
        ),
        ("色块标题页", [r"background canvas", r"title page", r"beamercolorbox"]),
        ("帧标题分割线", [r"frametitle", r"rule"]),
        (
            "页码（当前/总页数）",
            [r"insertframenumber", r"inserttotalframenumber", r"page of"],
        ),
        ("进度条", [r"progressbar", r"progress bar"]),
        (
            "深蓝 #00539F + 橙色强调，集中 color theme",
            [r"00539F", r"F5A623", r"usecolortheme", r"beamercolortheme"],
        ),
        (
            "block / alert / example 环境",
            [r"begin\{block\}", r"alertblock", r"exampleblock"],
        ),
        (
            "分节页 + 两栏 + 表格/图片/列表示例",
            [
                r"section page",
                r"columns",
                r"tabular",
                r"includegraphics",
                r"itemize|enumerate",
            ],
        ),
        ("section 拆分 + \\input + README", [r"\\input", r"README"]),
    ],
    "resume": [
        (
            "紧凑边距（geometry 1.5cm/2cm）",
            [
                r"top\s*=\s*1\.5cm",
                r"bottom\s*=\s*1\.5cm",
                r"left\s*=\s*2cm",
                r"right\s*=\s*2cm",
            ],
        ),
        (
            "无衬线字体（Source Sans/Roboto/默认 sans）",
            [r"Source Sans|Roboto|sffamily|setsansfont"],
        ),
        (
            "姓名大字号 + 粗细对比",
            [r"fontsize\{2[89]|fontsize\{3[0-9]", r"light|bfseries"],
        ),
        (
            "fontawesome 图标联系方式",
            [r"fontawesome", r"faEnvelope|faPhone|faGithub|faLinkedin"],
        ),
        ("分节标题 + 首字符主题色高亮", [r"cvsection", r"awesome|colorlet"]),
        (
            "时间右对齐 + 职位斜体 + 紧凑列表",
            [r"flushright|hfill", r"itshape|slshape", r"nosep|itemsep"],
        ),
        ("技能 tag 样式", [r"cvtag|cvskill|colorbox"]),
        ("主题色集中定义", [r"definecolor|colorlet"]),
        (
            ".cls 封装 + 语义命令（\\name/\\cventry/\\cvsection）",
            [r"\\name", r"cventry", r"cvsection"],
        ),
        ("单页（PDF 页数 ≤ 2）", []),  # 由 PDF 页数单独校验
    ],
    "requirements": [
        ("Letter + 1 英寸边距", [r"letterpaper", r"margin\s*=\s*1in"]),
        ("双栏排版", [r"twocolumn", r"multicols"]),
        ("10pt 正文", [r"10pt"]),
        ("标题 16pt 加粗 + 作者两列", [r"16pt", r"bfseries"]),
        ("Abstract/Keywords 样式", [r"Abstract", r"Keywords"]),
        (
            "定理/定义/例子共享编号",
            [r"newtheorem.*theorem|shared counter|numberwithin", r"newtheorem"],
        ),
        ("三线表 + 公式右对齐编号", [r"booktabs|toprule", r"equation"]),
        ("natbib author-year", [r"natbib", r"authoryear|author-year"]),
        ("深蓝 #1F4E79 主题色", [r"1F4E79"]),
        (".sty 封装 + README", [r"\.sty", r"README"]),
    ],
}


# 人类可读的检查点清单（由 REQUIREMENT_PATTERNS 标签自动派生，避免双份维护）
REQUIREMENT_CHECKS = {
    key: [label for label, _ in patterns]
    for key, patterns in REQUIREMENT_PATTERNS.items()
}
