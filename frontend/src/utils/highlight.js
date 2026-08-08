// 纯逻辑模块（无 CSS 副作用导入），可直接被 node:test 测试；
// token 颜色样式由使用方（CodeBlock / MarkerText）按需引入。

import hljs from "highlight.js/lib/core";
import latex from "highlight.js/lib/languages/latex";
import markdown from "highlight.js/lib/languages/markdown";
import python from "highlight.js/lib/languages/python";
import json from "highlight.js/lib/languages/json";
import xml from "highlight.js/lib/languages/xml";
import diff from "highlight.js/lib/languages/diff";
import plaintext from "highlight.js/lib/languages/plaintext";

/**
 * 轻量 BibTeX 语法：highlight.js v11 已移除内置 bibtex，这里本地补一个最小实现，
 * 覆盖 @entry、字段名、引号字符串与注释，满足参考文献管理的展示需求。
 */
function bibtex(hljs) {
  return {
    name: "BibTeX",
    case_insensitive: true,
    contains: [
      hljs.COMMENT("%", "$"),
      {
        className: "keyword",
        begin: /@[a-zA-Z]+/,
      },
      {
        className: "params",
        begin: /[a-zA-Z][\w-]*(?=\s*=\s*[{"])/,
      },
      {
        className: "string",
        begin: /"/,
        end: /"/,
      },
      {
        className: "punctuation",
        begin: /[{}]/,
      },
    ],
  };
}

// 按需注册语言：新增高亮语言时在这里注册即可，构建体积保持最小。
hljs.registerLanguage("latex", latex);
hljs.registerLanguage("tex", latex);
hljs.registerLanguage("bibtex", bibtex);
hljs.registerLanguage("markdown", markdown);
hljs.registerLanguage("python", python);
hljs.registerLanguage("json", json);
hljs.registerLanguage("xml", xml);
hljs.registerLanguage("html", xml);
hljs.registerLanguage("diff", diff);
hljs.registerLanguage("plaintext", plaintext);

const DEFAULT_LANGUAGE = "plaintext";
const INLINE_TAGS = new Set([
  "span",
  "b",
  "i",
  "em",
  "strong",
  "code",
  "mark",
  "sub",
  "sup",
  "u",
]);

/** 将任意语言标识归一化为已注册的语言名，未注册/为空时回退 plaintext。 */
export function normalizeLanguage(language) {
  const lang = String(language || "")
    .toLowerCase()
    .trim();
  return lang && hljs.getLanguage(lang) ? lang : DEFAULT_LANGUAGE;
}

/** 根据文件名后缀推断语言（.tex/.sty/.cls → latex，.bib → bibtex 等）。 */
export function languageFromFilename(filename = "") {
  const ext = String(filename).split(".").pop()?.toLowerCase() || "";
  const map = {
    tex: "latex",
    sty: "latex",
    cls: "latex",
    ltx: "latex",
    bib: "bibtex",
    md: "markdown",
    markdown: "markdown",
    py: "python",
    json: "json",
    html: "html",
    htm: "html",
    xml: "xml",
  };
  return map[ext] || DEFAULT_LANGUAGE;
}

/**
 * 将源码文本高亮为 HTML 字符串（渲染期派生，供 CodeBlock 与 MarkerText 直接注入）。
 * highlight.js 输出已转义，可安全用于 dangerouslySetInnerHTML。
 */
export function highlightHtml(text, language = DEFAULT_LANGUAGE) {
  const lang = normalizeLanguage(language);
  return hljs.highlight(String(text ?? ""), {
    language: lang,
    ignoreIllegals: true,
  }).value;
}

/** 转义 HTML 文本（用于拼接按行拆分的代码 HTML）。 */
export function escapeHtml(value) {
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

/**
 * 将代码高亮并按行拆分为 HTML 片段数组。
 *
 * highlight.js 输出的 span 可能跨行（如多行字符串）。输出是已转义的受控格式
 * （仅内联标签 + 转义文本），这里用纯字符串扫描按行拆分并重新闭合标签，
 * 保证每行是独立、自闭合的 HTML，供行号布局直接注入。
 */
export function highlightToHtmlLines(code, language = DEFAULT_LANGUAGE) {
  const text = String(code ?? "")
    .replace(/\r\n/g, "\n")
    .replace(/\r/g, "\n");
  if (!text) return [];
  const lang = normalizeLanguage(language);
  const html = hljs.highlight(text, {
    language: lang,
    ignoreIllegals: true,
  }).value;
  return splitHighlightedHtml(html);
}

/**
 * 将 highlight.js 的 HTML 输出按行拆分为自闭合片段。
 *
 * 约定输入格式：内容已由 highlight.js 转义，换行以字面 "\n" 出现，标签仅为
 * INLINE_TAGS 白名单内的内联元素（实际只有 span）。扫描时跟踪当前打开的标签：
 * 遇到换行先为当前行补上闭合标签、再以同样的开标签开启下一行，保证每行独立合法。
 * 未知标签按透明处理（忽略标签标记、保留内容），与旧的 DOM 兜底行为一致。
 */
function splitHighlightedHtml(html) {
  const lines = [];
  const openTags = [];
  let buf = "";
  let i = 0;

  const closeOpenTags = () => {
    for (let j = openTags.length - 1; j >= 0; j--) {
      buf += `</${openTags[j].name}>`;
    }
  };

  const flushLine = () => {
    closeOpenTags();
    lines.push(buf);
    // 下一行以当前仍打开标签的相同开标记开始，保证行内自闭合
    buf = openTags.map((tag) => tag.open).join("");
  };

  const openTag = (tagStr) => {
    const match = /^([a-zA-Z][\w-]*)([\s\S]*)$/.exec(tagStr.trim());
    if (!match) return;
    const name = match[1].toLowerCase();
    if (!INLINE_TAGS.has(name)) return; // 未知标签：忽略标记、保留内容
    openTags.push({ name, open: `<${tagStr}>` });
    buf += `<${tagStr}>`;
  };

  const closeTag = (tagStr) => {
    const name = tagStr.slice(1).trim().toLowerCase();
    if (!INLINE_TAGS.has(name)) return; // 与 openTag 的忽略策略对应
    if (openTags.length === 0 || openTags[openTags.length - 1].name !== name) {
      return; // 防御：只接受与栈顶匹配的闭合，避免破坏行内自闭合
    }
    openTags.pop();
    buf += `</${name}>`;
  };

  while (i < html.length) {
    const ch = html[i];
    if (ch === "\n") {
      flushLine();
      i++;
      continue;
    }
    if (ch === "<") {
      const gt = html.indexOf(">", i + 1);
      if (gt === -1) {
        buf += ch; // 防御：无闭合尖括号时按普通文本处理
        i++;
        continue;
      }
      const tagStr = html.slice(i + 1, gt);
      i = gt + 1;
      if (tagStr.startsWith("/")) closeTag(tagStr);
      else openTag(tagStr);
      continue;
    }
    buf += ch;
    i++;
  }
  flushLine();
  // 去掉末尾换行产生的空行（"a\n" 只显示一行）
  if (lines.length > 1 && lines[lines.length - 1] === "") lines.pop();
  return lines;
}
