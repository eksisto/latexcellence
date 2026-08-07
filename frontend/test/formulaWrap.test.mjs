import test from "node:test";
import assert from "node:assert/strict";

import {
  stripMathWrappers,
  wrapLatexForPaste,
} from "../src/utils/formulaWrap.js";

test("stripMathWrappers 剥离 $$ / \\[\\] / \\(\\) / $ 包裹", () => {
  assert.equal(stripMathWrappers("$$x^2$$"), "x^2");
  assert.equal(stripMathWrappers("\\[x^2\\]"), "x^2");
  assert.equal(stripMathWrappers("\\(x^2\\)"), "x^2");
  assert.equal(stripMathWrappers("$x^2$"), "x^2");
});

test("stripMathWrappers 剥离空壳显示环境（与后端 _WRAPPER_ENVS 一致）", () => {
  assert.equal(
    stripMathWrappers("\\begin{equation} E = mc^2 \\end{equation}"),
    "E = mc^2",
  );
  assert.equal(stripMathWrappers("\\begin{equation*} E \\end{equation*}"), "E");
  assert.equal(
    stripMathWrappers("\\begin{displaymath} E \\end{displaymath}"),
    "E",
  );
  assert.equal(
    stripMathWrappers("$$\\begin{equation} E \\end{equation}$$"),
    "E",
  );
});

test("wrapLatexForPaste 行内/行间包裹切换", () => {
  assert.equal(wrapLatexForPaste("\\[x^2\\]", true), "$x^2$");
  assert.equal(wrapLatexForPaste("$x^2$", false), "\\[x^2\\]");
  assert.equal(wrapLatexForPaste("x^2", true), "$x^2$");
  assert.equal(wrapLatexForPaste("x^2", false), "\\[x^2\\]");
});

test("回归：宏包注释前缀在切换时保留且包裹不嵌套", () => {
  const display = "% \\usepackage{amsmath}\n\\[x = 1 \\text{ for } x > 0\\]";
  const inline = wrapLatexForPaste(display, true);
  assert.equal(inline, "% \\usepackage{amsmath}\n$x = 1 \\text{ for } x > 0$");
  assert.equal(wrapLatexForPaste(inline, false), display);
  assert.equal(wrapLatexForPaste(inline, true), inline);
});

test("多行宏包注释全部保留", () => {
  const latex =
    "% \\usepackage{amsmath}\n% \\usepackage{mathtools}\n\\[x = 1\\]";
  assert.equal(
    wrapLatexForPaste(latex, true),
    "% \\usepackage{amsmath}\n% \\usepackage{mathtools}\n$x = 1$",
  );
});

test("独立显示环境原样保留（含注释前缀）", () => {
  const latex = "% \\usepackage{amsmath}\n\\begin{align} a &= b \\end{align}";
  assert.equal(wrapLatexForPaste(latex, true), latex);
  assert.equal(wrapLatexForPaste(latex, false), latex);
});

test("空字符串原样返回", () => {
  assert.equal(wrapLatexForPaste("", true), "");
  assert.equal(wrapLatexForPaste("   ", false), "");
});
