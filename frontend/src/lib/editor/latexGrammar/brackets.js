/**
 * LaTeX 子集语法的括号匹配精确化（editor v3 阶段六b / 决策 Q14）。
 *
 * 两层实现：
 *   1. **节点配对 prop**（latexBracketNodeProps）：把 { } / [ ] / ( ) /
 *      $ $$ \( \[ 等定界符节点经 @lezer/common 的 NodeProp.closedBy /
 *      openedBy 声明配对关系，挂到 parser.configure 的 props 上。CM6
 *      既有 bracketMatching（basicSetup 默认启用）的 matchBrackets 会
 *      优先读这两个 prop 并在「同一父节点的兄弟序列」内配对——花括号
 *      不会跨出 Group / MathGroup 作用域、`$` 定界不会跨对吞并、verbatim
 *      体内无括号节点天然豁免，文本扫描的误配对随之消失（配不上
 *      就不高亮，不做文本回退猜测）。
 *   2. **环境配对高亮**（latexEnvPairHighlight）：\begin{env} ↔ \end{env}
 *      的名称配对无法用 prop 表达（同 folding.js 头注），由本模块的
 *      ViewPlugin 在光标邻接环境定界节点时经 folding.js 纯函数求配对，
 *      复用 CM 括号匹配的 cm-matchingBracket 样式类（baseTheme 既有
 *      视觉，零新增 CSS）。
 *
 * prop 声明在组装层而非 grammar 源：@lezer/generator 的 grammar 文件
 * 不支持外部包 node prop（内置 prop 仅 name/dialect/inline 等），且
 * grammar 生成物保持零 @codemirror 依赖（node --test 可直测）——
 * parser.configure({props}) 是 CM 官方语言包（lang-html 等）的惯例位置。
 */

import { NodeProp } from "@lezer/common";
import { Decoration, ViewPlugin } from "@codemirror/view";
import { syntaxTree } from "@codemirror/language";

import { findEnvPairAt } from "./folding.js";

/**
 * 分组 / 数学定界符节点的配对 prop（值 = 配对节点的 node name 数组，
 * matchMarkedBrackets 按兄弟序列内的节点名匹配）。
 */
export const latexBracketNodeProps = [
  NodeProp.closedBy.add({
    OpenBrace: ["CloseBrace"],
    OpenBracket: ["CloseBracket"],
    OpenParen: ["CloseParen"],
    // 数学定界（MathDollar 自身同名配对：InlineMath 恰有两个 MathDollar 子节点）
    MathDollar: ["MathDollar"],
    MathDollarDollar: ["MathDollarDollar"],
    DBracketOpen: ["DBracketClose"],
    DParenOpen: ["DParenClose"],
  }),
  NodeProp.openedBy.add({
    CloseBrace: ["OpenBrace"],
    CloseBracket: ["OpenBracket"],
    CloseParen: ["OpenParen"],
    MathDollar: ["MathDollar"],
    MathDollarDollar: ["MathDollarDollar"],
    DBracketClose: ["DBracketOpen"],
    DParenClose: ["DParenOpen"],
  }),
];

/** 与 CM bracketMatching 一致的高亮装饰（复用 baseTheme 的视觉类） */
const matchingMark = Decoration.mark({ class: "cm-matchingBracket" });

/** 单次光标状态的配对高亮装饰集 */
function envPairDecorations(state) {
  const decorations = [];
  for (const range of state.selection.ranges) {
    if (!range.empty) continue;
    const pair = findEnvPairAt(syntaxTree(state), state.doc, range.head);
    if (!pair) continue;
    decorations.push(matchingMark.range(pair.begin.from, pair.begin.to));
    decorations.push(matchingMark.range(pair.end.from, pair.end.to));
  }
  return Decoration.set(decorations, true);
}

/**
 * \begin{env} ↔ \end{env} 配对高亮扩展（随 LanguageSupport 挂载，仅
 * Lezer 语法缓冲区生效）。与既有 bracketMatching（文本/prop 配对 {} 等）
 * 并存：两者装饰对象不同节点，不冲突。
 */
export const latexEnvPairHighlight = ViewPlugin.fromClass(
  class {
    constructor(view) {
      this.decorations = envPairDecorations(view.state);
    }
    update(update) {
      if (update.docChanged || update.selectionSet || update.viewportChanged) {
        this.decorations = envPairDecorations(update.state);
      }
    }
  },
  {
    decorations: (plugin) => plugin.decorations,
  },
);
