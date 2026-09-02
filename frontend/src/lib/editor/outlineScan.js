/**
 * 大纲扫描纯模块（editor-productivity-plan §5.3 阶段三 / 决策 Q6 / §4）。
 *
 * 输入缓冲区全文，解析 `\section` / `\subsection` / `\subsubsection` /
 * `\paragraph`（含星号变体），输出条目数组
 * `{ level, title, line }`：
 *   - level：section 0 / subsection 1 / subsubsection 2 / paragraph 3
 *     （大纲面板按此四级缩进展示）；
 *   - title：净化后的纯文本标题（规则见 titleToPlainText）；
 *   - line：1 基行号，供点击滚动定位。
 *
 * 边界处理（实现层既定决策）：
 *   - 注释：非转义 `%`（含 `\%` 转义判定，`\\%` 为真注释）之后的内容
 *     不产生条目；
 *   - 可选参数：`\section[短标题]{长标题}` 跳过 `[...]` 取强制参数；
 *   - 标题命令净化：命令名后紧跟花括号参数时丢弃命令名保留参数内容
 *     （`\textbf{粗}` → 粗）；无参数命令保留命令名去反斜杠
 *     （`\LaTeX` → LaTeX）；转义符号字面化（`\%` → %）；`~` 转空格；
 *     连续空白折叠为单个空格；
 *   - verbatim 类环境（verbatim / verbatim* / Verbatim / Verbatim* /
 *     lstlisting / minted，保守集合）内不产生条目；`\verb` 行内参数
 *     同样排除；
 *   - 跨行标题（`{` 未在同一行配平）保守跳过，不产生条目。
 *
 * 性能：逐行单趟扫描 + 无嵌套量词的正则，整体 O(n)。
 */

/** 分节命令 → 大纲层级（长名在前，防止 subsection 误吞 section 前缀） */
const SECTION_COMMAND_RE =
  /\\(subsubsection|subsection|section|paragraph)(?![a-zA-Z])/g;

/** 分节命令名 → 缩进层级 */
const LEVEL_BY_NAME = {
  section: 0,
  subsection: 1,
  subsubsection: 2,
  paragraph: 3,
};

/** verbatim 类环境起点（`\begin{...}`；环境名区分大小写） */
const BEGIN_VERBATIM_RE =
  /\\begin\s*\{(verbatim\*?|Verbatim\*?|lstlisting|minted)\}/;

/**
 * 截掉一行中非转义 `%` 之后的内容（LaTeX 注释）。
 * `%` 之前连续反斜杠个数为奇数视为转义百分号（`\%` 字面量，非注释），
 * 偶数（含 0）则为注释起点——因此 `\\%`（换行命令后的真注释）正确截断。
 * @param {string} line 单行文本
 */
export function stripComment(line) {
  for (let i = 0; i < line.length; i += 1) {
    if (line[i] !== "%") continue;
    let slashes = 0;
    for (let j = i - 1; j >= 0 && line[j] === "\\"; j -= 1) slashes += 1;
    if (slashes % 2 === 0) return line.slice(0, i);
  }
  return line;
}

/**
 * 移除一行中所有 `\verb`（含 `\verb*`）行内参数，防止其参数文本
 * （如 `\verb|\section{x}|`）被误认成大纲条目。
 * @param {string} line 已剥离注释的单行文本
 */
function stripVerbSpans(line) {
  let out = "";
  let i = 0;
  for (;;) {
    const idx = line.indexOf("\\verb", i);
    if (idx === -1) {
      out += line.slice(i);
      return out;
    }
    out += line.slice(i, idx);
    let j = idx + 5;
    if (line[j] === "*") j += 1;
    const delim = line[j];
    if (delim === undefined || /[a-zA-Z\s]/.test(delim)) {
      // 非法 verb 形态（定界符缺失/为字母）：原样保留已输出部分并跳过
      out += line.slice(idx, j + 1);
      i = j + 1;
      continue;
    }
    const end = line.indexOf(delim, j + 1);
    // 未闭合：余下内容整体视为 verb 内部，全部排除
    i = end === -1 ? line.length : end + 1;
  }
}

/**
 * 读取 line 中自 openIdx（指向 `{`）起的配平花括号组，返回组内文本
 * （不含首尾花括号）；未配平（如跨行标题）返回 null。
 * `\{` / `\}` 等转义字符不参与配平计数。
 */
function readBalancedGroup(line, openIdx) {
  let depth = 0;
  for (let i = openIdx; i < line.length; i += 1) {
    const ch = line[i];
    if (ch === "\\") {
      i += 1; // 跳过转义字符的下一个字符
      continue;
    }
    if (ch === "{") depth += 1;
    else if (ch === "}") {
      depth -= 1;
      if (depth === 0) return line.slice(openIdx + 1, i);
    }
  }
  return null;
}

/**
 * 标题原文 → 展示用纯文本（实现层既定决策，见模块头注释）：
 *   - 命令名后紧跟花括号参数：丢弃命令名，参数内容由外层继续处理
 *     （`\textbf{粗}` → 粗）；
 *   - 无参数命令：保留命令名去反斜杠（`\LaTeX` → LaTeX）；
 *   - 转义符号（`\%` / `\_` / `\{` 等）：保留字面字符；
 *   - `~`：空格；花括号定界符丢弃；连续空白折叠为单个空格。
 * @param {string} raw 花括号组内的标题原文
 */
export function titleToPlainText(raw) {
  if (typeof raw !== "string") return "";
  let out = "";
  for (let i = 0; i < raw.length; i += 1) {
    const ch = raw[i];
    if (ch === "\\") {
      const next = raw[i + 1];
      if (next === undefined) break;
      if (/[a-zA-Z]/.test(next)) {
        let j = i + 1;
        while (j < raw.length && /[a-zA-Z]/.test(raw[j])) j += 1;
        if (raw[j] === "*") j += 1;
        let k = j;
        while (raw[k] === " " || raw[k] === "\t") k += 1;
        if (raw[k] === "{") {
          // 带参数命令：丢弃命令名（参数内容与其后的 `}` 由主循环处理）
          i = j;
        } else {
          out += raw.slice(i + 1, j);
          i = j - 1;
        }
      } else {
        out += next;
        i += 1;
      }
      continue;
    }
    if (ch === "{" || ch === "}") continue;
    if (ch === "~") {
      out += " ";
      continue;
    }
    out += ch;
  }
  return out.replace(/\s+/g, " ").trim();
}

/**
 * 在已净化的单行文本中收集分节条目（内部函数）。
 * @param {string} line 已剥离注释与 verb 参数的单行文本
 * @param {number} lineNo 1 基行号
 * @param {Array<{level:number,title:string,line:number}>} entries 输出数组
 */
function collectSectionEntries(line, lineNo, entries) {
  SECTION_COMMAND_RE.lastIndex = 0;
  let m;
  while ((m = SECTION_COMMAND_RE.exec(line)) !== null) {
    let i = m.index + m[0].length;
    if (line[i] === "*") i += 1; // 星号变体（\section*）
    // 可选参数 [...]（短标题）：跳过后取强制参数
    if (line[i] === "[") {
      const close = line.indexOf("]", i + 1);
      if (close === -1) continue;
      i = close + 1;
    }
    while (line[i] === " " || line[i] === "\t") i += 1;
    if (line[i] !== "{") continue;
    const raw = readBalancedGroup(line, i);
    if (raw === null) continue; // 跨行标题等未配平场景：保守跳过
    // 标题内部若再出现分节命令字样（如注释性文字），不重复计入
    SECTION_COMMAND_RE.lastIndex = i + raw.length + 2;
    const title = titleToPlainText(raw);
    if (title)
      entries.push({ level: LEVEL_BY_NAME[m[1]], title, line: lineNo });
  }
}

/**
 * 扫描缓冲区全文，产出大纲条目（保持文档出现顺序）。
 * @param {string} docText 缓冲区全文（`\n` 换行）
 * @returns {Array<{level:number,title:string,line:number}>}
 */
export function scanOutline(docText) {
  const entries = [];
  if (typeof docText !== "string" || docText.length === 0) return entries;
  /** 当前所处 verbatim 类环境名（null = 不在 verbatim 内） */
  let verbatimEnv = null;
  const lines = docText.split("\n");
  for (let i = 0; i < lines.length; i += 1) {
    if (verbatimEnv) {
      // verbatim 内一切字面（含 % 注释），只找环境结束标记
      if (
        new RegExp(`\\\\end\\s*\\{${verbatimEnv.replace("*", "\\*")}\\}`).test(
          lines[i],
        )
      ) {
        verbatimEnv = null;
      }
      continue;
    }
    // 剥离顺序（缺一不可）：先剥 \verb 行内参数（其参数内 % 为字面字符、
    // \begin{verbatim} 为字面文本，均不产生注释/环境语义），再剥注释
    // （被注释掉的 \begin{verbatim} 不应进入 verbatim 态），最后检查
    // verbatim 环境起点（起点之后同行内容均在 verbatim 内）
    let work = stripVerbSpans(lines[i]);
    work = stripComment(work);
    const beginMatch = work.match(BEGIN_VERBATIM_RE);
    if (beginMatch) {
      verbatimEnv = beginMatch[1];
      work = work.slice(0, beginMatch.index);
    }
    collectSectionEntries(work, i + 1, entries);
  }
  return entries;
}
