/**
 * LaTeX 子集语法的环境体自动缩进纯函数层（editor v3 阶段六b / 决策 Q14）。
 *
 * 零外部依赖（鸭子类型消费 tree / doc，见 folding.js 头注），node --test
 * 直测；组装层经 indentService 接线（index.js）。
 *
 * ── 为什么走 indentService 而非 indentNodeProp（选型说明）────────────────
 *   indentNodeProp 的树路径缩进（syntaxIndentation）只看「包含目标位置的
 *   节点及其祖先链」上的 prop。本语法没有 Environment 包裹节点
 *   （\begin/\end 是兄弟节点且名称配对无法用 CFG 表达，见 folding.js 头注），
 *   BeginEnvironment 永远不会出现在环境体位置的祖先链上，indentNodeProp
 *   无从挂载；故经 indentService 按树查询计算（CM6 中 indentService 优先
 *   于树路径缩进，返回 null 即「保持现状」）。
 *
 * 规则（Overleaf 式）：
 *   - 环境体内换行 / 输入 `\end{`（indentOnInput 语言数据触发）时：
 *     缩进列 = 最内层开环境 `\begin` 的列 + 1 个缩进单位；
 *   - 行首为 `\end` 时：缩进列 = 该 `\begin` 的列（与起始对齐）；
 *   - 嵌套环境自然叠加（继承最内层 begin 行缩进再 +1 单位）；
 *   - verbatim 类环境体内返回 null（原样保留，不参与缩进——既定口径）；
 *   - 无开环境 / 回溯超限返回 null（保持现状，不越权改写顶层缩进）。
 */

import { findInnermostOpenEnv } from "./folding.js";

/**
 * 文本 upto 前的列号（tab 按 tabSize 展开进位，与 CM countColumn 同义）。
 * @param {string} text 行文本
 * @param {number} upto 目标下标（\begin 起点在该行内的偏移）
 * @param {number} tabSize
 * @returns {number}
 */
export function indentColumnAt(text, upto, tabSize) {
  let column = 0;
  const end = Math.min(upto, text.length);
  for (let i = 0; i < end; i += 1) {
    const ch = text[i];
    if (ch === "\t") column += tabSize - (column % tabSize);
    else column += 1;
  }
  return column;
}

/**
 * 计算 pos 处的自动缩进列。
 * @param {object} tree 解析树（parser.parse 产物）
 * @param {object} doc docLike（lineAt / sliceString / length）
 * @param {number} pos 待缩进行的目标位置（行首或换行后位置）
 * @param {number} unitColumns 缩进单位列宽（getIndentUnit(state)，默认 2）
 * @param {number} tabSize 制表符宽度（state.tabSize）
 * @returns {number|null} 缩进列；null = 不发表意见（保持现状）
 */
export function latexIndentColumn(tree, doc, pos, unitColumns, tabSize) {
  if (tree.length < pos) return null; // 解析未及（增量解析瞬态）→ 保持现状
  // verbatim 环境体不参与缩进：VerbatimEnvironment 是全环境包裹节点，
  // 祖先链检查即可命中。resolveInner 的 side 语义（-1 = from < pos ≤ to、
  // 1 = from ≤ pos < to）在 pos 恰处节点边界时各漏一侧，两侧都查。
  for (const side of [-1, 1]) {
    for (let node = tree.resolveInner(pos, side); node; node = node.parent) {
      if (node.name === "VerbatimEnvironment") return null;
    }
  }
  const open = findInnermostOpenEnv(tree, doc, pos);
  if (!open) return null;
  const beginLine = doc.lineAt(open.from);
  const base = indentColumnAt(
    beginLine.text,
    open.from - beginLine.from,
    tabSize,
  );
  // 行首已打出 \end（含部分形态 \end{）：与起始行对齐；否则进入一层
  const line = doc.lineAt(Math.min(pos, doc.length));
  if (/^\s*\\end\b/.test(line.text)) return base;
  return base + unitColumns;
}
