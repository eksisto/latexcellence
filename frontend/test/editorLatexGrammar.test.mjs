/**
 * 自研 Lezer LaTeX 子集语法解析树断言（editor v3 阶段六a / 设计文档 §4.6
 * / 决策 Q14）。被测对象为 latexGrammar 包的生成产物 parser.js（经 npm
 * pretest 钩子先行生成）与 highlight.js 的 tags 映射；language.js 的后缀
 * 分流契约一并覆盖。
 *
 * ── 与 stex 高亮的回归对照清单（验收要点）────────────────────────────────
 * 关键 token 类别的新旧 tag 映射对应（颜色均由 LaTeXEditor.jsx 的
 * latexHighlightStyle 统一映射到 --code-token-* 变量）：
 *
 * | token 类别                                   | stex StreamLanguage  | 本语法 node → @lezer/highlight tag     | 最终变量             |
 * | -------------------------------------------- | -------------------- | -------------------------------------- | -------------------- |
 * | 行注释                                       | comment              | LineComment → tags.comment             | --code-token-comment |
 * | 命令名 / 控制符号 / \begin \end \verb 命令名  | tag                  | Command、ControlSymbol、Begin/End/Verb* → tags.tagName | --code-token-title |
 * | 分节命令（\section 等）                       | tag                  | SectionCommand → tags.heading          | --code-token-title（heading 与 tagName 映射到同一变量，视觉一致） |
 * | 数学定界符 $ $$ \( \[ 及闭合                  | keyword              | MathDollar 系 → tags.keyword           | --code-token-keyword |
 * | 数学界内字母                                 | variableName.special | MathWord → tags.special(variableName)  | --code-token-string  |
 * | 数学界内数字                                 | number               | MathNumber → tags.number               | --code-token-number  |
 * | 数学界内 ^ _ &                               | tag                  | MathOp → tags.tagName                  | --code-token-title   |
 * | 花括号 / 方括号 / 圆括号                     | bracket              | Open/CloseBrace·Bracket·Paren → tags.bracket | --code-token-punctuation |
 * | 环境名（\begin{...} 的 ...）                  | atom（begin/end 参数）| EnvName → tags.atom                    | --code-token-built-in |
 * | 顶层数字                                     | atom                 | NumberLit → tags.atom                  | --code-token-built-in |
 * | 顶层英文单词 / 数学界内普通符号              | 无（DEFAULT → null） | 裸 token，无 tag                       | 无                   |
 * | \verb 参数 / verbatim 环境体                 | 无感知（stex 会把体内命令误着色） | 不打 tag，原样吞掉          | 无                   |
 * | .bib                                         | stex 全量            | 不适用（.bib 仍整体走 stex StreamLanguage） | 零回归           |
 *
 * 有意差异（两处，均在本子阶段接受）：
 *   1. verbatim 环境体：stex 继续常规解析（体内 \cmd 会误着色），本语法
 *      原样吞掉不着色——与 outlineScan / diagnostics 的 verbatim 豁免口径
 *      一致，属设计要求（§4.6）；
 *   2. `50%` 类序列：stex 把 `%` 连同数字一起着 atom，本语法按 LaTeX 语义
 *      `%` 起注释（digit 判定不再吞 `%`）。
 *
 * 已知口径差异（见 latex.grammar 头注，不追求 catcode 级正确 / `$` 配对
 * 零误判，依赖 Lezer 错误恢复）：`\begin {verbatim}`（命令名与花括号间带
 * 空白）不进入 verbatim 原样态；本测试对畸形输入只断言「可解析、全覆盖、
 * 不崩溃」，不断言具体恢复形态。
 */

import test from "node:test";
import assert from "node:assert/strict";

import { parser } from "../src/lib/editor/latexGrammar/parser.js";
import { latexStyleProps } from "../src/lib/editor/latexGrammar/highlight.js";
import {
  fileSuffixOf,
  latexLanguageExtension,
} from "../src/lib/editor/language.js";
import { highlightTree, classHighlighter } from "@lezer/highlight";

/** 与 latexGrammar/index.js 相同的 tags 配置方式（不依赖 @codemirror/*） */
const styledParser = parser.configure({ props: latexStyleProps });

/** 收集指定名称节点的 [from, to] 区间 */
function nodeSpans(tree, name) {
  const out = [];
  tree.iterate({
    enter: (node) => {
      if (node.name === name) out.push([node.from, node.to]);
    },
  });
  return out;
}

/** 树中是否含错误节点（Lezer 错误恢复插入，名称 "⚠"） */
function hasErrorNode(tree) {
  let found = false;
  tree.iterate({
    enter: (node) => {
      if (node.name === "⚠") found = true;
    },
  });
  return found;
}

/** 解析并经 classHighlighter 输出 [from, to, class] 高亮区间 */
function highlightSpans(doc) {
  const tree = styledParser.parse(doc);
  const out = [];
  highlightTree(tree, [classHighlighter], (from, to, cls) =>
    out.push([from, to, cls]),
  );
  return out;
}

test("行注释：非转义 % 至行尾，\\% 为字面字符", () => {
  const doc = "前言 % 行内注释 \\textbf{不解析}\n% 整行注释\n50\\% 字面";
  const tree = parser.parse(doc);
  assert.deepEqual(nodeSpans(tree, "LineComment"), [
    [3, 22],
    [23, 29],
  ]);
  // 注释内的 \textbf 不产生命令节点
  assert.deepEqual(nodeSpans(tree, "Command"), []);
  // \% 是 ControlSymbol（命令类），其后不是注释
  assert.deepEqual(nodeSpans(tree, "ControlSymbol"), [[32, 34]]);
});

test("行注释：\\\\% 为换行命令后的真注释（与 outlineScan.stripComment 同口径）", () => {
  const doc = "\\\\% 真注释";
  const tree = parser.parse(doc);
  assert.deepEqual(nodeSpans(tree, "ControlSymbol"), [[0, 2]]);
  assert.deepEqual(nodeSpans(tree, "LineComment"), [[2, 7]]);
  assert.deepEqual(highlightSpans(doc), [
    [0, 2, "tok-typeName"],
    [2, 7, "tok-comment"],
  ]);
});

test("命令：命令名着色、花括号参数成组、星号变体归入分节命令", () => {
  const doc = "\\textbf{粗体} 与 \\LaTeX \\section*{无编号}";
  const tree = parser.parse(doc);
  assert.deepEqual(nodeSpans(tree, "Command"), [
    [0, 7],
    [14, 20],
  ]);
  assert.deepEqual(nodeSpans(tree, "Group"), [
    [7, 11],
    [30, 35],
  ]);
  // \section* 特化为分节命令（SectionCommand），不落 Command
  assert.deepEqual(nodeSpans(tree, "SectionCommand"), [[21, 30]]);
  const classes = highlightSpans(doc);
  assert.deepEqual(classes[0], [0, 7, "tok-typeName"]);
  assert.ok(
    classes.some(([f, t, c]) => f === 21 && t === 30 && c === "tok-heading"),
  );
});

test("数学区：四种定界形态与邻接公式", () => {
  const doc = "$a+b_1$ 文本 $$x = y$$ 文本 \\[\\frac{a}{b}\\] 文本 \\(x^2\\)";
  const tree = parser.parse(doc);
  assert.deepEqual(nodeSpans(tree, "InlineMath"), [[0, 7]]);
  assert.deepEqual(nodeSpans(tree, "DisplayDollar"), [[11, 20]]);
  assert.deepEqual(nodeSpans(tree, "DisplayBracketMath"), [[24, 39]]);
  assert.deepEqual(nodeSpans(tree, "DisplayParenMath"), [[43, 50]]);
  // 数学界内叶子：字母 / 数字 / 上下标
  assert.deepEqual(nodeSpans(tree, "MathWord"), [
    [1, 2],
    [3, 4],
    [13, 14],
    [17, 18],
    [32, 33],
    [35, 36],
    [45, 46],
  ]);
  assert.deepEqual(nodeSpans(tree, "MathNumber"), [
    [5, 6],
    [47, 48],
  ]);
  assert.deepEqual(nodeSpans(tree, "MathOp"), [
    [4, 5],
    [46, 47],
  ]);
  // 数学界内 \frac 仍是命令节点
  assert.deepEqual(nodeSpans(tree, "Command"), [[26, 31]]);
});

test("数学区：邻接公式独立成对，不跨对吞并", () => {
  const doc = "$a$ 文本 $b$";
  const tree = parser.parse(doc);
  assert.deepEqual(nodeSpans(tree, "InlineMath"), [
    [0, 3],
    [7, 10],
  ]);
  const doc2 = "$$x$$ 文本 $$y$$";
  const tree2 = parser.parse(doc2);
  assert.deepEqual(nodeSpans(tree2, "DisplayDollar"), [
    [0, 5],
    [9, 14],
  ]);
});

test("数学区高亮：定界符 keyword / 字母 special / 数字 number，与 stex 对齐", () => {
  const doc = "$a+b_1$";
  assert.deepEqual(highlightSpans(doc), [
    [0, 1, "tok-keyword"],
    [1, 2, "tok-variableName2"],
    [3, 4, "tok-variableName2"],
    [4, 5, "tok-typeName"],
    [5, 6, "tok-number"],
    [6, 7, "tok-keyword"],
  ]);
});

test("环境：\\begin/\\end 配对与环境名（atom 着色）", () => {
  const doc = "\\begin{figure}[h] 内容 \\end{figure}";
  const tree = parser.parse(doc);
  assert.deepEqual(nodeSpans(tree, "BeginEnvironment"), [[0, 14]]);
  assert.deepEqual(nodeSpans(tree, "EndEnvironment"), [[21, 33]]);
  assert.deepEqual(nodeSpans(tree, "EnvName"), [
    [7, 13],
    [26, 32],
  ]);
  const classes = highlightSpans(doc);
  assert.ok(
    classes.some(([f, t, c]) => f === 7 && t === 13 && c === "tok-atom"),
  );
});

test("环境：align* 等带星号环境名", () => {
  const doc = "\\begin{align*}x\\end{align*}";
  const tree = parser.parse(doc);
  assert.deepEqual(nodeSpans(tree, "BeginEnvironment"), [[0, 14]]);
  assert.deepEqual(nodeSpans(tree, "EndEnvironment"), [[15, 27]]);
});

test("verbatim 类环境：六种环境名整体原样吞掉", () => {
  const body = "% 注释 $x$ \\textbf{y}\n正文";
  for (const env of [
    "verbatim",
    "verbatim*",
    "Verbatim",
    "Verbatim*",
    "lstlisting",
    "minted",
  ]) {
    const doc = `\\begin{${env}}${body}\\end{${env}}\\LaTeX`;
    const tree = parser.parse(doc);
    const spans = nodeSpans(tree, "VerbatimEnvironment");
    assert.equal(spans.length, 1, env);
    const [from, to] = spans[0];
    assert.equal(
      doc.slice(from, to),
      `\\begin{${env}}${body}\\end{${env}}`,
      env,
    );
    // 体内容零语义：无注释 / 命令 / 数学节点（起止标记为独立包装节点）
    const inside = (name) =>
      nodeSpans(tree, name).filter(([f]) => f > from && f < to);
    assert.deepEqual(inside("LineComment"), [], env);
    assert.deepEqual(inside("Command"), [], env);
    assert.deepEqual(inside("MathDollar"), [], env);
    assert.deepEqual(inside("InlineMath"), [], env);
    // 环境后的内容恢复正常解析（\LaTeX 为命令节点）
    assert.ok(
      nodeSpans(tree, "Command").some(([f, t]) => f >= to),
      env,
    );
  }
});

test("verbatim：体内容不着色（与 stex 误着色的有意差异）", () => {
  const doc = "\\begin{verbatim}$x$ %c\\end{verbatim}之后";
  const spans = highlightSpans(doc);
  const bodyStart = "\\begin{verbatim}".length;
  const bodyEnd = doc.indexOf("\\end{verbatim}");
  const bodySpans = spans.filter(([f, t]) => f >= bodyStart && t <= bodyEnd);
  assert.deepEqual(bodySpans, []);
  // 起止字面标记按命令类着色
  assert.ok(
    spans.some(
      ([f, t, c]) => f === 0 && t === bodyStart && c === "tok-typeName",
    ),
  );
});

test("verbatim：未闭合环境不崩溃、全覆盖并在结尾留错误恢复", () => {
  const doc = "\\begin{verbatim}\n% $ \\cmd\n之后无 end";
  const tree = parser.parse(doc);
  assert.equal(tree.length, doc.length);
  assert.deepEqual(nodeSpans(tree, "VerbatimEnvironment"), [[0, doc.length]]);
  assert.ok(hasErrorNode(tree));
});

test("\\verb：任意相同定界符参数原样吞掉（含 $ 与 % 字面）", () => {
  const doc = "a \\verb|xy| b \\verb!z$z! c \\verb#%x# d";
  const tree = parser.parse(doc);
  assert.deepEqual(nodeSpans(tree, "VerbCommand"), [
    [2, 11],
    [14, 24],
    [27, 36],
  ]);
  // 参数内的 $ 不产生数学节点、% 不产生注释
  assert.deepEqual(nodeSpans(tree, "InlineMath"), []);
  assert.deepEqual(nodeSpans(tree, "Command"), []);
  assert.deepEqual(nodeSpans(tree, "LineComment"), []);
});

test("\\verb：星号变体与非法定界符", () => {
  const doc = "\\verb*|q| 与 \\verb x";
  const tree = parser.parse(doc);
  // 非法定界符（空格）在空格处即触发错误恢复：命令 span 不含尾随空格
  //（VerbCommand 定义于空跳过组，ws 不被吞入命令）
  assert.deepEqual(nodeSpans(tree, "VerbCommand"), [
    [0, 9],
    [12, 17],
  ]);
  // 定界符为空格：非法形态走错误恢复，不崩溃
  assert.equal(tree.length, doc.length);
  assert.ok(hasErrorNode(tree));
});

test("\\verb：% 定界符（真实 LaTeX 与 outlineScan.stripVerbSpans 均允许）", () => {
  // `%` 作定界符时 verbReader 依赖空跳过组介入：若 VerbCommand 在默认
  // 组内解析，verbArg 期待态下 `%` 被主 token 表命中为 LineComment（skip
  // token 在该状态有合法动作，token 读取循环终止），fallback 永不被询问，
  // 参数退化为注释吞掉。回归覆盖 review-fix：VerbCommand 移入 @skip {} 组。
  const doc = "A \\verb%ab% B % 真注释";
  const tree = parser.parse(doc);
  // 参数恰为 %ab%，其后 B 恢复正常解析，不被牵连进注释
  assert.deepEqual(nodeSpans(tree, "VerbCommand"), [[2, 11]]);
  // 行尾真注释独立成立（% 真注释）
  assert.deepEqual(nodeSpans(tree, "LineComment"), [[14, 19]]);
  assert.ok(!hasErrorNode(tree));
  // 高亮：命令名着 tag；%ab% 参数不着色；真注释着 comment
  const classes = highlightSpans(doc);
  assert.ok(
    classes.some(([f, t, c]) => f === 2 && t === 7 && c === "tok-typeName"),
  );
  assert.ok(
    classes.some(([f, t, c]) => f === 14 && t === 19 && c === "tok-comment"),
  );
  assert.deepEqual(
    classes.filter(([f]) => f >= 7 && f < 11),
    [],
  );
});

test("\\verb：未闭合时行内吞到行尾、跨行内容不受牵连", () => {
  const doc = "\\verb|abc\nafter| x";
  const tree = parser.parse(doc);
  assert.deepEqual(nodeSpans(tree, "VerbCommand"), [[0, 9]]);
  assert.equal(tree.length, doc.length);
});

test("\\verb：文档末尾未闭合吞到末行行尾", () => {
  const doc = "a \\verb|abc";
  const tree = parser.parse(doc);
  assert.deepEqual(nodeSpans(tree, "VerbCommand"), [[2, doc.length]]);
});

test("\\verb 高亮：命令名着 tag，参数不着色", () => {
  const doc = "\\verb|xy|";
  assert.deepEqual(highlightSpans(doc), [[0, 5, "tok-typeName"]]);
});

test("分节命令：六命令 + 星号变体全部映射 heading tag", () => {
  const doc = [
    "\\part{甲}",
    "\\chapter{乙}",
    "\\section{一}",
    "\\subsection{二}",
    "\\subsubsection{三}",
    "\\paragraph{注}",
    "\\section*{无编号}",
  ].join("\n");
  const tree = parser.parse(doc);
  assert.equal(nodeSpans(tree, "SectionCommand").length, 7);
  // 可选参数 [...] 落为普通 token，不破坏命令识别
  const doc2 = "\\section[短]{长标题}";
  assert.deepEqual(nodeSpans(parser.parse(doc2), "SectionCommand"), [[0, 8]]);
  // heading 高亮区间恰好覆盖命令名（含星号）
  const doc3 = "\\section*{标题}";
  assert.deepEqual(highlightSpans(doc3), [
    [0, 9, "tok-heading"],
    [9, 10, "tok-punctuation"],
    [12, 13, "tok-punctuation"],
  ]);
});

test("分组：花括号嵌套成组，顶层数字为 NumberLit", () => {
  const doc = "{外 {内} 层} 42";
  const tree = parser.parse(doc);
  assert.deepEqual(nodeSpans(tree, "Group"), [
    [0, 9],
    [3, 6],
  ]);
  assert.deepEqual(nodeSpans(tree, "NumberLit"), [[10, 12]]);
});

test("上下标：数学界内 MathOp 节点，顶层裸 token 无节点（无色）", () => {
  const tree = parser.parse("$a^b_c$");
  assert.deepEqual(nodeSpans(tree, "MathOp"), [
    [2, 3],
    [4, 5],
  ]);
  // 顶层 ^ _ 解析为裸 mathOpTok（不建节点、不着色），不崩溃
  const top = parser.parse("a^b_c");
  assert.equal(top.length, 5);
  assert.deepEqual(nodeSpans(top, "MathOp"), []);
});

test("分组高亮：括号 bracket、内容不着色", () => {
  const doc = "{中文}";
  assert.deepEqual(highlightSpans(doc), [
    [0, 1, "tok-punctuation"],
    [3, 4, "tok-punctuation"],
  ]);
});

test("畸形输入：错误恢复保证可解析且全覆盖（不崩溃）", () => {
  const cases = [
    "$$$ { \\begin % \\end", // 定界符连环失控
    "5 美元 $ 未闭合", // 未配对 $
    "{ { { }", // 花括号失衡
    "\\begin{my_env}x\\end{my_env}", // 环境名含 _（超出命令名字符集）
    "\\begin{figure} 缺少 \\end", // 环境未闭合
    "\\subsection{", // 截断
    "\\verb", // 截断的 verb
    "\\\\]}", // 多余闭合符
  ];
  for (const doc of cases) {
    const tree = parser.parse(doc);
    assert.equal(tree.length, doc.length, JSON.stringify(doc));
    assert.ok(
      hasErrorNode(tree),
      `期望存在错误恢复节点: ${JSON.stringify(doc)}`,
    );
  }
});

test("\\begin {verbatim}（带空白）：退化普通环境解析（既定口径差异）", () => {
  const doc = "\\begin {verbatim}x\\end{verbatim}";
  const tree = parser.parse(doc);
  assert.deepEqual(nodeSpans(tree, "VerbatimEnvironment"), []);
  assert.deepEqual(nodeSpans(tree, "BeginEnvironment"), [[0, 17]]);
  assert.deepEqual(nodeSpans(tree, "EndEnvironment"), [[18, 32]]);
  assert.ok(!hasErrorNode(tree));
});

test("中文正文与全角标点：整篇无错误节点", () => {
  const doc = "这是中文正文，包含标点、英文 mixed 与 数字 42。";
  const tree = parser.parse(doc);
  assert.equal(tree.length, doc.length);
  assert.ok(!hasErrorNode(tree));
});

test("language.js 后缀分流：.tex/.sty/.cls 走 Lezer，.bib 与未知走 stex", () => {
  const tex = latexLanguageExtension(".tex");
  const sty = latexLanguageExtension(".sty");
  const cls = latexLanguageExtension(".cls");
  const bib = latexLanguageExtension(".bib");
  const unknown = latexLanguageExtension("");
  assert.equal(tex, sty);
  assert.equal(sty, cls);
  assert.notEqual(tex, bib);
  assert.equal(bib, unknown); // 未知后缀回落 stex，与 .bib 同实例
  // .bib 路径不受 Lezer 语法影响（四类后缀高亮不回归要点）
  assert.notEqual(
    latexLanguageExtension(".tex"),
    latexLanguageExtension(".bib"),
  );
});

test("language.js fileSuffixOf：路径 → 小写后缀", () => {
  assert.equal(fileSuffixOf("main.tex"), ".tex");
  assert.equal(fileSuffixOf("chapters/intro.TEX"), ".tex");
  assert.equal(fileSuffixOf("refs.bib"), ".bib");
  assert.equal(fileSuffixOf("style.cls"), ".cls");
  assert.equal(fileSuffixOf("noext"), "");
  assert.equal(fileSuffixOf(".hidden"), "");
  assert.equal(fileSuffixOf(""), "");
  assert.equal(fileSuffixOf(null), "");
});

test("大文档解析：2MB 单次全量解析可完成（耗时数据点，见 docs/modules/editor.md）", () => {
  // 代表性混排段落：命令 / 数学 / 环境 / verbatim / 注释 / 中文
  const paragraph = [
    "\\section{第 %d 节}",
    "正文段落，包含 \\textbf{粗体} 与行内公式 $a^{2}+b_{1}=c$，以及",
    "\\begin{itemize}\\item 条目一 \\item 条目二\\end{itemize}。",
    "\\begin{verbatim}raw %content $x$\\end{verbatim}",
    "% 行注释 \\section{不解析}",
    "",
  ].join("\n");
  const target = 2 * 1024 * 1024;
  const repeats = Math.ceil(target / paragraph.length);
  const doc = paragraph.repeat(repeats);
  assert.ok(doc.length >= target);
  const start = performance.now();
  const tree = parser.parse(doc);
  const elapsedMs = Math.round(performance.now() - start);
  assert.equal(tree.length, doc.length);
  console.log(
    `[latexGrammar 大文档解析] ${doc.length} 字符（约 ${(doc.length / 1024 / 1024).toFixed(2)} MB）全量解析耗时 ${elapsedMs} ms`,
  );
  // 宽松上限防回归（CM6 实际键入为增量解析；「2MB 键入不卡」需真机验证，
  // 此处仅佐证单次全量解析的量级可接受）。20s 上限在慢速 CI（实测 ~2s）
  // 与 10 倍极限节流下均有余量，只拦截算法级退化。
  assert.ok(elapsedMs < 20000, `2MB 解析耗时异常: ${elapsedMs} ms`);
});
