/**
 * LaTeX 子集语法的高亮 tags 映射（editor v3 阶段六a / 决策 Q14）。
 *
 * 只对"叶子节点"打 tag：@lezer/highlight 的 highlightRange 会把外层节点
 * 样式 class 沿树向内累积，容器节点（InlineMath / Group / VerbatimBody 等）
 * 一律不着色，由叶子节点各自映射，保证每个区间恰好一个 class。
 *
 * tag 选型与 stex StreamLanguage 的视觉表现逐类对齐（回归对照清单见
 * test/editorLatexGrammar.test.mjs 头注；颜色由 LaTeXEditor.jsx 的
 * latexHighlightStyle 统一映射到 --code-token-* 变量）：
 *   - LineComment → comment            （同 stex "comment"）
 *   - Command / ControlSymbol / Begin/End/Verb 命令名 → tagName（同 "tag"）
 *   - 数学定界符 → keyword              （同 stex 数学定界 "keyword"）
 *   - MathWord → special(variableName) （同 stex 数学字母 "variableName.special"）
 *   - MathNumber → number              （同 stex 数学数字 "number"）
 *   - MathOp（^ _ &）→ tagName         （同 stex 数学特殊符 "tag"）
 *   - 括号类 → bracket                 （同 stex "bracket"）
 *   - EnvName / NumberLit → atom       （同 stex "atom"）
 *   - SectionCommand → heading          （计划要求；latexHighlightStyle 将其
 *     映射到 --code-token-title，与 stex 分节命令的 "tag" 视觉一致）
 *   - VerbArg / VerbatimBody → 不着色   （同 stex 对 verb/verbatim 的无感知）
 */

import { styleTags, tags as t } from "@lezer/highlight";

export const latexStyleProps = [
  styleTags({
    LineComment: t.comment,

    Command: t.tagName,
    ControlSymbol: t.tagName,
    BeginCmd: t.tagName,
    EndCmd: t.tagName,
    BeginV: t.tagName,
    EndV: t.tagName,
    BeginVStar: t.tagName,
    EndVStar: t.tagName,
    BeginVUpper: t.tagName,
    EndVUpper: t.tagName,
    BeginVUpperStar: t.tagName,
    EndVUpperStar: t.tagName,
    BeginLst: t.tagName,
    EndLst: t.tagName,
    BeginMinted: t.tagName,
    EndMinted: t.tagName,
    VerbCmdT: t.tagName,
    VerbStarT: t.tagName,

    SectionCommand: t.heading,

    MathDollar: t.keyword,
    MathDollarDollar: t.keyword,
    DBracketOpen: t.keyword,
    DBracketClose: t.keyword,
    DParenOpen: t.keyword,
    DParenClose: t.keyword,

    MathWord: t.special(t.variableName),
    MathNumber: t.number,
    MathOp: t.tagName,

    OpenBrace: t.bracket,
    CloseBrace: t.bracket,
    OpenBracket: t.bracket,
    CloseBracket: t.bracket,
    OpenParen: t.bracket,
    CloseParen: t.bracket,

    EnvName: t.atom,
    NumberLit: t.atom,
  }),
];
