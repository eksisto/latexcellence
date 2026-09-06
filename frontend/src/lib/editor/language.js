/**
 * LaTeX 语言包抽象层（editor-design §3 / 决策 Q2；editor v3 阶段六a /
 * 决策 Q14；阶段六b 折叠与环境缩进）。
 *
 * 设计约束：
 *   **本文件是语言包进入编辑器的唯一入口**——其余代码不得直接 import
 *   语法实现（legacy-modes / latexGrammar），将来替换语法实现只需改写
 *   本文件，保持 `latexLanguageExtension()` 的返回契约（LanguageSupport /
 *   StreamLanguage 扩展）不变。
 *
 * 后缀分流（Q14）：
 *   - `.tex` / `.sty` / `.cls` → 自研 MIT Lezer 子集语法
 *     （latexGrammar/，精确高亮 + Lezer 增量解析；六b 起含环境 / 分节
 *     折叠（foldService）、环境体自动缩进（indentService + indentOnInput）
 *     与基于语法树的括号匹配精确化（openedBy/closedBy prop + \begin↔\end
 *     配对高亮））；
 *   - `.bib` 及未知后缀 → 沿用 @codemirror/legacy-modes 的 stex 流式
 *     模式（自研最小 bib 语法列为可选尾项，暂不实施）。
 *
 * `latexFoldingExtension()` 转发折叠 UI 扩展组（foldGutter + 折叠占位符，
 * 见 latexGrammar/index.js 的 latexFoldingUI）：编辑器按 codeFolding 偏好
 * 条件挂载（扩展模块级单例，切换经换档失配重建生效）。
 *
 * 高亮配色复用全局 `--code-token-*` CSS 变量（LaTeXEditor.jsx 的
 * latexHighlightStyle），亮/暗主题切换时无需重新构建编辑器扩展。
 */

import { StreamLanguage } from "@codemirror/language";
import { stexMath } from "@codemirror/legacy-modes/mode/stex";

import { latexLanguageSupport, latexFoldingUI } from "./latexGrammar/index.js";

/** 走自研 Lezer 语法的文件后缀集合（.bib 暂不在此列，见 Q14） */
const LEZER_SUFFIXES = new Set([".tex", ".sty", ".cls"]);

/** 缓存语言扩展实例：语言对象可跨 EditorState 复用，避免重复构造 */
let lezerSupport = null;
let stexLanguage = null;

/**
 * 从文件路径提取小写后缀（含点，如 ".tex"；无后缀返回空串）。
 * @param {string|null} path 文件路径（tab 的 filePath）
 * @returns {string}
 */
export function fileSuffixOf(path) {
  if (typeof path !== "string" || path.length === 0) return "";
  const dot = path.lastIndexOf(".");
  if (dot <= 0) return "";
  return path.slice(dot).toLowerCase();
}

/**
 * 返回 LaTeX 语言扩展（按文件后缀分流，见模块头注）。
 * @param {string} [suffix] 文件后缀（fileSuffixOf 的返回值，如 ".tex"）；
 *   缺省/未知后缀回落 stex 流式模式，与既有调用方零风险兼容
 * @returns {import("@codemirror/language").LanguageSupport |
 *   import("@codemirror/language").StreamLanguage<unknown>}
 */
export function latexLanguageExtension(suffix = "") {
  if (LEZER_SUFFIXES.has(suffix)) {
    if (!lezerSupport) {
      lezerSupport = latexLanguageSupport();
    }
    return lezerSupport;
  }
  if (!stexLanguage) {
    // stexMath：对 $...$ / $$...$$ 数学区做更细的高亮区分
    stexLanguage = StreamLanguage.define(stexMath);
  }
  return stexLanguage;
}

/**
 * 返回折叠 UI 扩展组（foldGutter + codeFolding 折叠占位符；模块级单例）。
 * 仅 Lezer 语法提供折叠区间（foldService / foldNodeProp 随语言挂载），
 * .bib 缓冲区挂载后无折叠标记、行为与现状一致。
 * @returns {import("@codemirror/state").Extension[]}
 */
export function latexFoldingExtension() {
  if (!foldingExtension) {
    foldingExtension = latexFoldingUI();
  }
  return foldingExtension;
}
let foldingExtension = null;
