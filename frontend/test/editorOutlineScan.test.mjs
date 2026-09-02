import test from "node:test";
import assert from "node:assert/strict";

import {
  scanOutline,
  titleToPlainText,
  stripComment,
} from "../src/lib/editor/outlineScan.js";

test("scanOutline：四级条目 + 行号 + 星号变体", () => {
  const doc = [
    "\\documentclass{article}",
    "\\begin{document}",
    "\\section{引言}",
    "正文",
    "\\subsection{背景}",
    "\\subsubsection{动机}",
    "text",
    "\\paragraph{备注}",
    "\\section*{无编号节}",
    "\\end{document}",
  ].join("\n");
  const outline = scanOutline(doc);
  assert.deepEqual(
    outline.map((e) => [e.level, e.title, e.line]),
    [
      [0, "引言", 3],
      [1, "背景", 5],
      [2, "动机", 6],
      [3, "备注", 8],
      [0, "无编号节", 9],
    ],
  );
});

test("scanOutline：注释行与行内注释不产生条目", () => {
  const doc = [
    "% \\section{被整行注释}",
    "\\section{真节} % 行内注释 \\subsection{不出现}",
    "50\\% 转义百分号 \\section{转义后仍在}",
    "\\\\% 换行命令后真注释 \\section{不出现}",
  ].join("\n");
  const outline = scanOutline(doc);
  assert.deepEqual(
    outline.map((e) => e.title),
    ["真节", "转义后仍在"],
  );
});

test("scanOutline：可选参数被跳过，取强制参数标题", () => {
  const doc =
    "\\section[短标题]{长标题在这里}\n\\subsection[short]{sub 标题}\n";
  const outline = scanOutline(doc);
  assert.deepEqual(
    outline.map((e) => e.title),
    ["长标题在这里", "sub 标题"],
  );
});

test("scanOutline：标题内嵌套花括号正确配对", () => {
  const doc = "\\section{嵌套 {内层 {最内层}} 标题}\n";
  const outline = scanOutline(doc);
  assert.equal(outline.length, 1);
  assert.equal(outline[0].title, "嵌套 内层 最内层 标题");
});

test("scanOutline：标题命令净化（带参数丢名留参 / 无参留名 / 转义符号）", () => {
  const doc = [
    "\\section{The \\textbf{Bold} Title}",
    "\\subsection{关于 \\LaTeX 的说明}",
    "\\subsubsection{a\\_b 与 50\\%}",
    "\\paragraph{\\texorpdfstring{A}{B}}",
  ].join("\n");
  const outline = scanOutline(doc);
  assert.deepEqual(
    outline.map((e) => e.title),
    ["The Bold Title", "关于 LaTeX 的说明", "a_b 与 50%", "AB"],
  );
});

test("scanOutline：verbatim 类环境内不产生条目，环境结束后恢复", () => {
  const doc = [
    "\\section{环境前}",
    "\\begin{verbatim}",
    "\\subsection{verbatim 内}",
    "\\end{verbatim}",
    "\\section{环境后}",
    "\\begin{lstlisting}[language=Python]",
    "\\subsection{lstlisting 内}",
    "\\end{lstlisting}",
    "\\subsection{最后}",
  ].join("\n");
  const outline = scanOutline(doc);
  assert.deepEqual(
    outline.map((e) => e.title),
    ["环境前", "环境后", "最后"],
  );
});

test("scanOutline：\\verb 行内参数中的分节命令不产生条目", () => {
  const doc =
    "\\section{正常}\n\\verb|\\subsection{假条目}|\n\\paragraph{末尾}\n";
  const outline = scanOutline(doc);
  assert.deepEqual(
    outline.map((e) => e.title),
    ["正常", "末尾"],
  );
});

test("scanOutline：\\verb 参数内 % 为字面字符，不触发注释截断（回归）", () => {
  // 真实 LaTeX 中 \verb|...| 参数内的 % 是字面量而非注释起点；若先剥注释
  // 会误截断并让未闭合 verb 吞掉行尾真实条目
  const doc = "\\section{a} \\verb|http://x%y| \\section{b}\n";
  assert.deepEqual(
    scanOutline(doc).map((e) => e.title),
    ["a", "b"],
  );
});

test("scanOutline：\\verb 参数内的 \\begin{verbatim} 不进入 verbatim 态（回归）", () => {
  // \begin{verbatim} 出现在 \verb 参数中只是字面文本，不得开启环境吞掉
  // 后续文档的大纲
  const doc = "\\verb|\\begin{verbatim}| \\section{after}\n\\section{next}\n";
  assert.deepEqual(
    scanOutline(doc).map((e) => e.title),
    ["after", "next"],
  );
});

test("scanOutline：跨行标题（花括号未配平）保守跳过", () => {
  const doc = "\\section{第一行\n  延续到第二行}\n\\subsection{正常}\n";
  const outline = scanOutline(doc);
  assert.deepEqual(
    outline.map((e) => e.title),
    ["正常"],
  );
});

test("scanOutline：非标题形态不产生条目", () => {
  const doc = [
    "\\sectioning{非分节命令}",
    "\\subsectionflag{x}",
    "plain text \\section",
    "\\section 没有花括号",
    "\\mysection{自定义命令}",
  ].join("\n");
  assert.deepEqual(scanOutline(doc), []);
});

test("scanOutline：空输入与空标题", () => {
  assert.deepEqual(scanOutline(""), []);
  assert.deepEqual(scanOutline(null), []);
  assert.deepEqual(scanOutline("\\section{}"), []);
  assert.deepEqual(scanOutline("\\section{   }"), []);
});

test("scanOutline：星号后接可选参数的混合形态", () => {
  const doc = "\\section*[短]{带星可选}\n";
  const outline = scanOutline(doc);
  assert.deepEqual(
    outline.map((e) => [e.level, e.title]),
    [[0, "带星可选"]],
  );
});

test("scanOutline：大文档单趟扫描结果正确（性能形态哨兵）", () => {
  // 1 万行、1 千个分节命令，无嵌套量词正则下单趟扫描应即时完成
  const lines = new Array(10000).fill("plain text line");
  for (let i = 0; i < 1000; i += 1) {
    lines[i * 10] = `\\section{第 ${i} 节}`;
  }
  const doc = lines.join("\n");
  const start = Date.now();
  const outline = scanOutline(doc);
  const elapsed = Date.now() - start;
  assert.equal(outline.length, 1000);
  assert.equal(outline[0].line, 1);
  assert.equal(outline[999].line, 999 * 10 + 1);
  assert.ok(elapsed < 2000, `大文档扫描耗时过长：${elapsed}ms`);
});

test("stripComment：转义判定与 preambleScan 同语义", () => {
  assert.equal(stripComment("abc % tail"), "abc ");
  assert.equal(stripComment("abc \\% not comment"), "abc \\% not comment");
  assert.equal(stripComment("abc \\\\% tail"), "abc \\\\");
});
