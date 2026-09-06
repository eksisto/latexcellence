/**
 * LaTeX 子集语法 LanguageSupport 组装（editor v3 阶段六a / 决策 Q14）。
 *
 * parser.js / parser.terms.js 为 lezer-generator 生成物（不入库），由
 * latexGrammar/build.mjs 在 dev/build/test 前生成；本模块将生成 parser
 * 与 highlight.js 的 tags 映射组装为 CM6 LanguageSupport。
 *
 * 本模块（连带 @codemirror/language 依赖）只在浏览器侧经 language.js
 * 唯一入口进入编辑器；node --test 直接测 parser.js / highlight.js /
 * verbTokens.js（无需 @codemirror/*）。
 */

import { LRLanguage, LanguageSupport } from "@codemirror/language";
import { parser } from "./parser.js";
import { latexStyleProps } from "./highlight.js";

const latexParser = parser.configure({ props: latexStyleProps });

export const latexLanguage = LRLanguage.define({
  parser: latexParser,
  // 行注释切换（toggleLineComment）与 stex StreamLanguage 的
  // commentTokens 口径一致
  languageData: { commentTokens: { line: "%" } },
});

/** 返回 LaTeX 语言支持（.tex / .sty / .cls 共用同一实例）。 */
let cachedSupport = null;
export function latexLanguageSupport() {
  if (!cachedSupport) {
    cachedSupport = new LanguageSupport(latexLanguage);
  }
  return cachedSupport;
}
