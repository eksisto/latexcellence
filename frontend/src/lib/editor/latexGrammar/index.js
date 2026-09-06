/**
 * LaTeX 子集语法 LanguageSupport 组装（editor v3 阶段六a P0 高亮 / 六b
 * 折叠·缩进·括号匹配 / 决策 Q14）。
 *
 * parser.js / parser.terms.js 为 lezer-generator 生成物（不入库），由
 * latexGrammar/build.mjs 在 dev/build/test 前生成；本模块将生成 parser
 * 与各层扩展组装为 CM6 LanguageSupport：
 *   - highlight.js 的 styleTags（叶子包裹着色，六a）；
 *   - brackets.js 的 openedBy/closedBy 配对 prop + \begin↔\end 配对高亮
 *     插件（六b，括号匹配精确化）；
 *   - foldNodeProp（verbatim 环境折叠，六b）；
 *   - foldService（环境 / 分节折叠，配对逻辑在 folding.js 纯函数层，六b）；
 *   - indentService（环境体自动缩进，逻辑在 indentation.js 纯函数层，六b；
 *     缩进单位沿袭项目现状——未显式设定 indentUnit，即 CM 默认 2 空格，
 *     getIndentUnit 动态取值不另行覆写）；
 *   - languageData：行注释切换（六a）+ indentOnInput 触发正则（六b：
 *     打出 `\end` 时按缩进服务回正该行，与 \begin 打字机补 \end 联动）。
 *
 * 折叠 / 缩进为何走 service 而非 nodeProp：见 folding.js / indentation.js
 * 头注选型说明（无 Environment 包裹节点 + 环境名配对 CFG 不可表达）。
 *
 * 本模块（连带 @codemirror/* 依赖）只在浏览器侧经 language.js 唯一入口
 * 进入编辑器；node --test 亦可直接导入（@codemirror/* 在 node 下可导入，
 * 六a 的 language.js 分流测试即先例），折叠 / 缩进 / 配对纯函数层另见
 * folding.js / indentation.js（零 @codemirror 依赖）。
 */

import {
  LRLanguage,
  LanguageSupport,
  codeFolding,
  foldGutter,
  foldNodeProp,
  foldService,
  getIndentUnit,
  indentService,
  syntaxTree,
} from "@codemirror/language";

import { parser } from "./parser.js";
import { latexStyleProps } from "./highlight.js";
import { latexBracketNodeProps, latexEnvPairHighlight } from "./brackets.js";
import {
  foldPlaceholderText,
  latexFoldRange,
  verbatimFoldRange,
} from "./folding.js";
import { latexIndentColumn } from "./indentation.js";

const latexParser = parser.configure({
  props: [
    ...latexStyleProps,
    ...latexBracketNodeProps,
    // verbatim 类环境有跨全环境的包裹节点，走 CM 原生 foldNodeProp 路径
    //（环境 / 分节折叠无对应包裹节点，走下方 foldService）
    foldNodeProp.add({
      VerbatimEnvironment: (node, state) => verbatimFoldRange(state.doc, node),
    }),
  ],
});

export const latexLanguage = LRLanguage.define({
  parser: latexParser,
  // 行注释切换（toggleLineComment）与 stex StreamLanguage 的
  // commentTokens 口径一致；indentOnInput 供 basicSetup 的 indentOnInput
  // 过滤器在打出 \end 时触发行重缩进（verbatim 内由缩进服务返回 null 豁免）
  languageData: {
    commentTokens: { line: "%" },
    indentOnInput: /^\s*\\end\b/,
  },
});

// 环境 / 分节折叠：逐行询问（foldGutter / foldCode / 括号匹配均经此入口）
const latexFoldService = foldService.of((state, lineStart, lineEnd) =>
  latexFoldRange(syntaxTree(state), state.doc, lineStart, lineEnd),
);

// 环境体自动缩进：indentService 优先于 CM 树路径缩进；返回 null = 保持现状
const latexIndentService = indentService.of((context, pos) =>
  latexIndentColumn(
    syntaxTree(context.state),
    context.state.doc,
    pos,
    getIndentUnit(context.state),
    context.state.tabSize,
  ),
);

/** 返回 LaTeX 语言支持（.tex / .sty/.cls 共用同一实例，含六b 扩展组）。 */
let cachedSupport = null;
export function latexLanguageSupport() {
  if (!cachedSupport) {
    cachedSupport = new LanguageSupport(latexLanguage, [
      latexFoldService,
      latexIndentService,
      latexEnvPairHighlight,
    ]);
  }
  return cachedSupport;
}

/**
 * 折叠 UI 扩展组（foldGutter + codeFolding 占位符配置）：占位符显示
 * 环境名 / 分节标题摘要（folding.js foldPlaceholderText），点击展开。
 * 由 language.js 经唯一入口转发给编辑器挂载（codeFolding 偏好开关在
 * 应用层控制挂载与否，扩展本身模块级单例）。
 * @returns {import("@codemirror/state").Extension[]}
 */
let cachedFoldingUI = null;
export function latexFoldingUI() {
  if (!cachedFoldingUI) {
    cachedFoldingUI = [
      foldGutter(),
      codeFolding({
        // 折叠发生时计算占位符摘要（随树状态缓存于折叠装饰内）
        preparePlaceholder: (state, range) =>
          foldPlaceholderText(syntaxTree(state), state.doc, range.from),
        placeholderDOM: (view, onclick, text) => {
          const span = document.createElement("span");
          span.textContent = typeof text === "string" && text ? text : "⋯";
          span.className = "cm-foldPlaceholder";
          span.setAttribute("aria-label", "folded code");
          span.title = "unfold";
          span.onclick = onclick;
          return span;
        },
      }),
    ];
  }
  return cachedFoldingUI;
}
