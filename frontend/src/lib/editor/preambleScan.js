/**
 * 导言区扫描与宏包缺失联动（editor-design §6.5 / 决策 Q14）。
 *
 * 纯函数层（node --test 直接覆盖）：
 *   - 导言区边界：`\begin{document}` 之前；文档无该标记时（.sty 等）
 *     视为整篇均需扫描；
 *   - 已加载宏包集合：解析导言区内 `\usepackage[opts]{a,b,c}`（含多包
 *     列表与可选参数），逐行剥离注释后再匹配，被注释掉的声明不计数；
 *   - 插入点：最后一条 usepackage 声明之后 → `\documentclass` 之后 →
 *     文档顶部，三级回退。
 *
 * 命令→必需宏包的最小映射来自数据库条目的 `pkg` 字段；后续如需扩大
 * 覆盖面可切换为后端宏包规则表接口供给，本模块接口不变。
 */

/** 匹配一条 usepackage 声明（不含注释；调用方负责先剥离行内注释） */
const USEPACKAGE_RE = /\\usepackage(?:\[[^\]\n]*\])?\{([^}\n]+)\}/g;
/** usepackage 声明行判定（插入点回退扫描用；模块级避免循环内重建） */
const USEPACKAGE_LINE_RE = /\\usepackage\b/;
/** 文档类声明 */
const DOCUMENTCLASS_RE = /\\documentclass(?:\[[^\]\n]*\])?\{[^}\n]+\}/;

/**
 * 返回导言区文本片段（`\begin{document}` 之前；无该标记则整篇）。
 * @param {string} docText 缓冲区全文
 */
export function preambleText(docText) {
  if (typeof docText !== "string" || docText.length === 0) return "";
  const idx = docText.indexOf("\\begin{document}");
  return idx === -1 ? docText : docText.slice(0, idx);
}

/**
 * 解析一段文本中出现的宏包名集合。逐行处理：
 * 先截掉非转义 `%` 起的行内注释，再做 usepackage 匹配。
 * @param {string} text 待解析文本
 * @returns {Set<string>} 小写宏包名
 */
export function parseLoadedPackages(text) {
  const loaded = new Set();
  if (typeof text !== "string") return loaded;
  for (const line of text.split("\n")) {
    const stripped = stripComment(line);
    USEPACKAGE_RE.lastIndex = 0;
    let m;
    while ((m = USEPACKAGE_RE.exec(stripped)) !== null) {
      for (const raw of m[1].split(",")) {
        const name = raw.trim().toLowerCase();
        if (name) loaded.add(name);
      }
    }
  }
  return loaded;
}

/**
 * 截掉一行中非转义 `%` 之后的内容（LaTeX 注释）。
 * `%` 之前连续反斜杠个数为奇数视为转义百分号（`\%` 字面量，非注释），
 * 偶数（含 0）则为注释起点——因此 `\\%`（换行命令后的真注释）正确截断。
 */
function stripComment(line) {
  for (let i = 0; i < line.length; i += 1) {
    if (line[i] !== "%") continue;
    let slashes = 0;
    for (let j = i - 1; j >= 0 && line[j] === "\\"; j -= 1) slashes += 1;
    if (slashes % 2 === 0) return line.slice(0, i);
  }
  return line;
}

/**
 * 当前缓冲区已加载的宏包集合（仅统计导言区）。
 * @param {string} docText
 */
export function extractPackages(docText) {
  return parseLoadedPackages(preambleText(docText));
}

/**
 * 宏包是否缺失：缓冲区导言区未加载指定宏包。
 * @param {string} docText
 * @param {string|null} pkg 必需宏包名（null 恒为不缺失）
 */
export function isPackageMissing(docText, pkg) {
  if (!pkg) return false;
  return !extractPackages(docText).has(pkg.toLowerCase());
}

/**
 * 在导言区中选取新 usepackage 声明的插入点（字符偏移，插入到该偏移之前）。
 * 三级回退：最后一条 usepackage 声明所在行的下一行行首 →
 * `\documentclass` 行的下一行行首 → 文档顶部（0）。
 * @param {string} docText
 * @returns {{index: number, anchor: "usepackage"|"documentclass"|"top"}}
 */
export function findUsepackageInsertPoint(docText) {
  if (typeof docText !== "string" || docText.length === 0) {
    return { index: 0, anchor: "top" };
  }
  const pre = preambleText(docText);
  const lines = pre.split("\n");

  let lastUsepackageLineEnd = -1;
  let classLineEnd = -1;
  for (let i = 0; i < lines.length; i += 1) {
    const stripped = stripComment(lines[i]);
    if (USEPACKAGE_LINE_RE.test(stripped)) lastUsepackageLineEnd = i;
    else if (classLineEnd === -1 && DOCUMENTCLASS_RE.test(stripped))
      classLineEnd = i;
  }

  // 偏移 = 各候选行（含换行符）累计长度
  const offsetAfterLine = (lineIdx) =>
    lines.slice(0, lineIdx + 1).reduce((sum, l) => sum + l.length + 1, 0);
  // 候选声明与 \begin{document} 同行时（如一行写完
  // `\usepackage{x}\begin{document}`），行末偏移会多计一个换行长度而
  // 越入文档体内部：钳制到 `\begin{document}` 起点之前（存在该串时取 min）
  const clampToPreamble = (offset) => {
    const docStart = docText.indexOf("\\begin{document}");
    return docStart === -1 ? offset : Math.min(offset, docStart);
  };

  if (lastUsepackageLineEnd >= 0) {
    return {
      index: clampToPreamble(offsetAfterLine(lastUsepackageLineEnd)),
      anchor: "usepackage",
    };
  }
  if (classLineEnd >= 0) {
    return {
      index: clampToPreamble(offsetAfterLine(classLineEnd)),
      anchor: "documentclass",
    };
  }
  return { index: 0, anchor: "top" };
}

/**
 * 生成一次完整的「添加宏包」编辑描述：插入点 + 插入文本。
 * 已存在同名宏包时返回 null（无需动作）。
 * @param {string} docText
 * @param {string} pkg
 * @returns {{index:number, text:string}|null}
 */
export function buildPackageInsertion(docText, pkg) {
  if (!pkg || !isPackageMissing(docText, pkg)) return null;
  const { index } = findUsepackageInsertPoint(docText);
  return { index, text: `\\usepackage{${pkg}}\n` };
}
