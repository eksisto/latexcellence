/**
 * LaTeX 语言包抽象层（editor-plan §3 / 决策 Q2）。
 *
 * 设计约束（Q2）：
 *   v1 使用 MIT 许可的 `@codemirror/legacy-modes` stex 流式模式，仅提供
 *   语法高亮；**本文件是语言包进入编辑器的唯一入口**。将来若整体替换为
 *   AGPL 的 Lezer 语法包（codemirror-lang-latex），只需改写本文件的实现、
 *   保持 `latexLanguageExtension()` 的返回契约（LanguageSupport / 扩展数组）
 *   不变，其余代码零改动。
 *
 * 高亮配色复用全局 `--code-token-*` CSS 变量（styles/code-highlight.css），
 * 亮/暗主题切换时无需重新构建编辑器扩展。
 */

import { StreamLanguage } from "@codemirror/language";
import { stexMath } from "@codemirror/legacy-modes/mode/stex";

/** 缓存语言扩展实例：StreamLanguage 状态可跨实例复用，避免重复构造 */
let latexLanguage = null;

/**
 * 返回 LaTeX 语言扩展（.tex / .bib / .sty / .cls 共用同一流式模式）。
 * @returns {import("@codemirror/language").StreamLanguage<unknown>}
 */
export function latexLanguageExtension() {
  if (!latexLanguage) {
    // stexMath：对 $...$ / $$...$$ 数学区做更细的高亮区分
    latexLanguage = StreamLanguage.define(stexMath);
  }
  return latexLanguage;
}
