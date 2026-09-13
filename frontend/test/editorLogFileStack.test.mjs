import test from "node:test";
import assert from "node:assert/strict";

import {
  parseLogErrors,
  normalizeProjectRelativePath,
} from "../src/lib/editor/logFileStack.js";

test("parseLogErrors：正常嵌套 + 跨文件错误归属栈顶", () => {
  const log = [
    "This is pdfTeX, Version 3.14159265 (preloaded format=pdflatex)",
    "(./main.tex (./chapters/intro.tex",
    "! Undefined control sequence.",
    "<argument> \\foo",
    "l.5 \\badcommand",
    ")",
    "! Missing $ inserted.",
    "l.9 \\sum x",
  ].join("\n");
  const errors = parseLogErrors(log, "main.tex");
  assert.deepEqual(
    errors.map((e) => [e.file, e.line, e.lowConfidence]),
    [
      ["chapters/intro.tex", 5, false],
      ["main.tex", 9, false],
    ],
  );
});

test("parseLogErrors：多层嵌套归属最内层，闭括号后回到外层", () => {
  const log = [
    "(./main.tex",
    "(./chapters/a.tex",
    "(./chapters/b.tex",
    "! LaTeX Error: Environment baz undefined.",
    "l.10 \\begin{baz}",
    ")",
    ")",
    "! Emergency stop.",
    "l.20 \\end{document}",
  ].join("\n");
  assert.deepEqual(
    parseLogErrors(log, "main.tex").map((e) => [e.file, e.line]),
    [
      ["chapters/b.tex", 10],
      ["main.tex", 20],
    ],
  );
});

test("parseLogErrors：输出条目携带消息与 l.N 源码回显 detail", () => {
  const log = "(./main.tex\n! Undefined control sequence.\nl.7 \\frak{x}\n";
  const [entry] = parseLogErrors(log, "main.tex");
  assert.equal(entry.file, "main.tex");
  assert.equal(entry.line, 7);
  assert.equal(entry.message, "Undefined control sequence.");
  assert.equal(entry.detail, "\\frak{x}");
  assert.equal(entry.lowConfidence, false);
});

test("parseLogErrors：无文件栈（单文件项目 / texfot 过滤）回落发起文件并标低置信", () => {
  const log = "! Undefined control sequence.\nl.3 \\oops\n";
  const errors = parseLogErrors(log, "main.tex");
  assert.deepEqual(errors, [
    {
      file: "main.tex",
      line: 3,
      message: "Undefined control sequence.",
      detail: "\\oops",
      lowConfidence: true,
    },
  ]);
});

test("parseLogErrors：无 fallback 时回落条目 file 为 null", () => {
  const errors = parseLogErrors("! Boom.\nl.2 x", null);
  assert.equal(errors[0].file, null);
  assert.equal(errors[0].lowConfidence, true);
});

test("parseLogErrors：栈顶系统文件（texmf 绝对路径）回落低置信", () => {
  const log = [
    "(./main.tex (/usr/share/texlive/texmf-dist/tex/latex/tools/longtable.sty",
    "! Package longtable Error: misaligned columns.",
    "l.42 \\bad",
  ].join("\n");
  const errors = parseLogErrors(log, "main.tex");
  assert.deepEqual(
    errors.map((e) => [e.file, e.lowConfidence]),
    [["main.tex", true]],
  );
});

test("parseLogErrors：栈顶非白名单文件（.aux）回落低置信", () => {
  const log = "(./main.tex (./main.aux\n! Bad aux file entry.\nl.4 \\relax\n";
  const errors = parseLogErrors(log, "main.tex");
  assert.deepEqual(
    errors.map((e) => [e.file, e.lowConfidence]),
    [["main.tex", true]],
  );
});

test("parseLogErrors：空栈弹栈（畸形日志）置 stackBroken，此后错误回落低置信", () => {
  const log = [
    ") stray close",
    "(./main.tex",
    "! Undefined control sequence.",
    "l.5 \\a",
  ].join("\n");
  const errors = parseLogErrors(log, "main.tex");
  assert.equal(errors.length, 1);
  assert.equal(errors[0].file, "main.tex");
  assert.equal(errors[0].lowConfidence, true);
});

test("parseLogErrors：文本括号（see the transcript / Overfull）不破坏文件归属", () => {
  const log = [
    "(./main.tex",
    "Overfull \\hbox (15.3pt too wide) in paragraph at lines 10--15",
    "(see the transcript file for additional information)",
    "! Undefined control sequence.",
    "l.8 \\x",
  ].join("\n");
  const errors = parseLogErrors(log, "main.tex");
  assert.deepEqual(
    errors.map((e) => [e.file, e.lowConfidence]),
    [["main.tex", false]],
  );
});

test("parseLogErrors：\\( / \\) 数学括号不参与栈运算", () => {
  const log = [
    "(./main.tex",
    "text with \\(math in parens\\) printed",
    "! Missing ( inserted.",
    "l.6 \\y",
  ].join("\n");
  const errors = parseLogErrors(log, "main.tex");
  assert.deepEqual(
    errors.map((e) => [e.file, e.lowConfidence]),
    [["main.tex", false]],
  );
});

test("parseLogErrors：消息内未配对括号（Missing ) inserted）被错误块隔离", () => {
  const log = [
    "(./main.tex (./chapters/intro.tex",
    "! Missing ) inserted.",
    "l.5 \\x",
    "! Undefined control sequence.",
    "l.6 \\y",
  ].join("\n");
  const errors = parseLogErrors(log, "main.tex");
  assert.deepEqual(
    errors.map((e) => [e.file, e.lowConfidence]),
    [
      ["chapters/intro.tex", false],
      ["chapters/intro.tex", false],
    ],
  );
});

test("parseLogErrors：79 列折行文件名跨行续接（dewrap）", () => {
  const longName = `./chapters/${"section-with-a-very-long-name-".repeat(2)}intro.tex`;
  const head = `(./main.tex (./${longName.slice(2)}`;
  // 构造 79 列折行：首段打满 79 列，剩余部分落在下一物理行
  const firstSegment = head.slice(0, 79);
  const rest = head.slice(79);
  const log = [
    firstSegment,
    rest,
    "! Undefined control sequence.",
    "l.3 \\z",
  ].join("\n");
  const errors = parseLogErrors(log, "main.tex");
  assert.equal(errors.length, 1);
  assert.equal(errors[0].file, longName.slice(2));
  assert.equal(errors[0].lowConfidence, false);
});

test("parseLogErrors：79 列折行错误消息跨行合并", () => {
  const msg =
    "Package babel Error: The option for the main language was not set correctly at all.";
  assert.ok(msg.length > 79);
  const log = [
    "(./main.tex",
    `! ${msg.slice(0, 77)}`, // `! ` + 77 字符 = 79 列折行段
    msg.slice(77),
    "l.12 \\z",
  ].join("\n");
  const [entry] = parseLogErrors(log, "main.tex");
  assert.equal(entry.file, "main.tex");
  assert.equal(entry.message, msg);
});

test("parseLogErrors：连续错误块互不串扰，新 ! 行接管", () => {
  const log = [
    "(./main.tex",
    "! Missing $ inserted.",
    "<inserted text>",
    "l.4 \\a",
    "! Missing } inserted.",
    "l.5 \\b",
  ].join("\n");
  assert.deepEqual(
    parseLogErrors(log, "main.tex").map((e) => [e.line, e.message]),
    [
      [4, "Missing $ inserted."],
      [5, "Missing } inserted."],
    ],
  );
});

test("parseLogErrors：无 l.N 错误块上下文收满后恢复栈扫描，不吞后续文件栈", () => {
  // 回归：`! File not found` 类无行号错误块曾无限吞行，导致后续
  // `(./chapters/intro.tex` 未压栈、后续错误误归属 main.tex 且标高置信
  const log = [
    "(./main.tex",
    "! LaTeX Error: File `missing.sty' not found.",
    "",
    "Type X to quit or <RETURN> to proceed,",
    "or enter new name. (Default extension: sty)",
    "Enter file name: ",
    "(./chapters/intro.tex",
    "! Undefined control sequence.",
    "l.5 \\x",
    ")",
    "! Missing $ inserted.",
    "l.9 \\sum",
  ].join("\n");
  assert.deepEqual(
    parseLogErrors(log, "main.tex").map((e) => [e.file, e.lowConfidence]),
    [
      ["chapters/intro.tex", false],
      ["main.tex", false],
    ],
  );
});

test("parseLogErrors：帮助文本空行不打断错误块，l.N 仍归属原文件", () => {
  // LaTeX 错误帮助文本含空行 + 三行帮助后才有 l.N，块不得提前作废
  const log = [
    "(./main.tex (./chapters/a.tex",
    "! LaTeX Error: Environment align undefined.",
    "",
    "See the LaTeX manual or LaTeX Companion for explanation.",
    "Type  H <return>  for immediate help.",
    " ...",
    "",
    "l.12 \\begin{align}",
  ].join("\n");
  const errors = parseLogErrors(log, "main.tex");
  assert.deepEqual(
    errors.map((e) => [e.file, e.line, e.lowConfidence]),
    [["chapters/a.tex", 12, false]],
  );
});

test("parseLogErrors：上下文收满仍未 l.N 的块保持开启，更晚的 l.N 仍记录", () => {
  const log = [
    "(./main.tex",
    "! Undefined control sequence.",
    "\\foo",
    "<argument> \\bar",
    "<recently read> \\baz",
    "\\qux",
    "l.5 \\x",
  ].join("\n");
  const errors = parseLogErrors(log, "main.tex");
  assert.deepEqual(
    errors.map((e) => [e.file, e.line, e.lowConfidence]),
    [["main.tex", 5, false]],
  );
});

test("parseLogErrors：未以 l.N 收尾的错误块被丢弃（无行号不可跳转）", () => {
  const log = "(./main.tex\n!  ==> Fatal error occurred, no output PDF file!\n";
  assert.deepEqual(parseLogErrors(log, "main.tex"), []);
});

test("parseLogErrors：条目数上限防御（MAX_ERRORS = 200）", () => {
  const log = ["(./main.tex"];
  for (let i = 0; i < 250; i += 1) {
    log.push(`! Error number ${i}.`, `l.${i + 1} \\x`);
  }
  assert.equal(parseLogErrors(log.join("\n"), "main.tex").length, 200);
});

test("parseLogErrors：空输入返回空数组", () => {
  assert.deepEqual(parseLogErrors("", "main.tex"), []);
  assert.deepEqual(parseLogErrors(null, "main.tex"), []);
  assert.deepEqual(parseLogErrors(undefined, null), []);
});

test("normalizeProjectRelativePath：./ 前缀剥离与不可跳转路径判空", () => {
  assert.equal(normalizeProjectRelativePath("./main.tex"), "main.tex");
  assert.equal(
    normalizeProjectRelativePath("././chapters/a.tex"),
    "chapters/a.tex",
  );
  assert.equal(
    normalizeProjectRelativePath("chapters/a.tex"),
    "chapters/a.tex",
  );
  assert.equal(normalizeProjectRelativePath("/usr/share/texmf/x.sty"), null);
  assert.equal(normalizeProjectRelativePath("../outside.tex"), null);
  assert.equal(normalizeProjectRelativePath("a/../b.tex"), null);
  assert.equal(normalizeProjectRelativePath(""), null);
  assert.equal(normalizeProjectRelativePath(null), null);
});

test("parseLogErrors：大日志单趟解析性能形态哨兵", () => {
  const lines = ["(./main.tex (./chapters/a.tex"];
  for (let i = 0; i < 5000; i += 1) {
    lines.push(
      `Overfull \\hbox (10pt too wide) in paragraph at lines ${i}--${i}`,
    );
  }
  lines.push("! Undefined control sequence.", "l.99 \\x");
  const start = Date.now();
  const errors = parseLogErrors(lines.join("\n"), "main.tex");
  const elapsed = Date.now() - start;
  assert.equal(errors.length, 1);
  assert.equal(errors[0].file, "chapters/a.tex");
  // 上限是退化哨兵而非性能指标：正常单趟解析远低于 1s，慢速 CI 也不过数秒；
  // 平方级退化会到分钟级。阈值取 8s 只拦截算法退化。
  assert.ok(elapsed < 8000, `大日志解析耗时过长：${elapsed}ms`);
});
