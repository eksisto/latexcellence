import test from "node:test";
import assert from "node:assert/strict";

import {
  DISPLAY_MATH_ENVS,
  findFormulaAt,
  isDisplayMathEnv,
} from "../src/lib/editor/formulaHoverScan.js";

/** 定位子串在文档中的位置（断言可读性辅助）。 */
function at(text, needle, occurrence = 0) {
  let idx = -1;
  for (let k = 0; k <= occurrence; k += 1) idx = text.indexOf(needle, idx + 1);
  assert.ok(idx !== -1, `fixture missing ${needle}`);
  return idx;
}

test("行内 $...$：区域内任意位置命中，返回全文与行内标记", () => {
  const doc = "before $a^2 + b_2$ after";
  const from = doc.indexOf("$");
  const to = doc.indexOf("$", from + 1) + 1;
  for (const pos of [from, from + 3, to - 1]) {
    const r = findFormulaAt(doc, pos);
    assert.ok(r, `pos=${pos}`);
    assert.equal(r.from, from);
    assert.equal(r.to, to);
    assert.equal(r.text, "$a^2 + b_2$");
    assert.equal(r.display, false);
  }
});

test("行内 \\(...\\) 命中", () => {
  const doc = "x \\(a+b\\) y";
  const r = findFormulaAt(doc, at(doc, "a+b"));
  assert.equal(r.text, "\\(a+b\\)");
  assert.equal(r.display, false);
});

test("display $$...$$ 与 \\[...\\] 命中且 display=true", () => {
  const d1 = "a $$x^2$$ b";
  const r1 = findFormulaAt(d1, at(d1, "x^2"));
  assert.equal(r1.text, "$$x^2$$");
  assert.equal(r1.display, true);

  const d2 = "a \\[x^2\\] b";
  const r2 = findFormulaAt(d2, at(d2, "x^2"));
  assert.equal(r2.text, "\\[x^2\\]");
  assert.equal(r2.display, true);
});

test("$$ 是 display，不当作两个相邻行内公式", () => {
  const doc = "$$x$$";
  // 压在第一个 $ 与第二个 $ 上都应命中同一 display 区域
  const r = findFormulaAt(doc, 1);
  assert.equal(r.from, 0);
  assert.equal(r.to, 5);
  assert.equal(r.text, "$$x$$");
  assert.equal(r.display, true);
});

test("equation / align 系环境命中（含换行），display=true", () => {
  const doc = "前文\n\\begin{equation}\n  E = mc^2\n\\end{equation}\n后文";
  const r = findFormulaAt(doc, at(doc, "mc^2"));
  assert.equal(r.display, true);
  assert.equal(r.text, "\\begin{equation}\n  E = mc^2\n\\end{equation}");
  assert.equal(r.from, doc.indexOf("\\begin{equation}"));
  assert.equal(r.to, doc.indexOf("\\end{equation}") + "\\end{equation}".length);
});

test("带星环境与全环境清单口径命中", () => {
  const doc = "\\begin{align*}\na &= b\n\\end{align*}";
  const r = findFormulaAt(doc, at(doc, "&= b"));
  assert.equal(r.display, true);
  assert.ok(r.text.startsWith("\\begin{align*}"));
  for (const env of ["gather", "multline", "eqnarray", "flalign", "alignat"]) {
    assert.equal(isDisplayMathEnv(env), true);
    assert.equal(isDisplayMathEnv(`${env}*`), true);
  }
  assert.equal(isDisplayMathEnv("aligned"), false);
  assert.equal(isDisplayMathEnv("cases"), false);
  assert.equal(DISPLAY_MATH_ENVS.length, 15);
});

test("同名环境嵌套按深度配对，内层悬停命中外层整体", () => {
  const doc = [
    "\\begin{align}",
    "a &= b \\\\",
    "\\begin{align}",
    "c &= d",
    "\\end{align}",
    "e &= f",
    "\\end{align}",
  ].join("\n");
  const r = findFormulaAt(doc, at(doc, "c &= d"));
  assert.equal(r.from, 0);
  assert.equal(r.to, doc.length);
  // 外层（非嵌套点）同样命中整体
  const r2 = findFormulaAt(doc, at(doc, "e &= f"));
  assert.equal(r2.to, doc.length);
});

test("\\$ 字面美元不算定界符", () => {
  const doc = "价格 \\$5 与 \\$6，共 $a+b$ 元";
  const r = findFormulaAt(doc, at(doc, "a+b"));
  assert.equal(r.text, "$a+b$");
  // 转义美元处不命中任何公式
  assert.equal(findFormulaAt(doc, at(doc, "与")), null);
});

test("悬停在 \\$ / \\\\ 转义对的字面字符上不判定公式（回归：跨过 pos 的转义对）", () => {
  // 悬停 \$ 的 $ 上：即使后文存在真正的 $，也不得把两段误连成公式
  const doc = "价格 \\$5 元，然后 $real$ 结束";
  assert.equal(findFormulaAt(doc, at(doc, "\\$")), null);
  // 悬停 \$ 的 \ 上同样不命中
  assert.equal(findFormulaAt(doc, at(doc, "\\$") - 1), null);

  // \\ 是字面反斜杠：其后紧跟 ( 不得把 (b\) 误判为 \(…\) 公式
  const doc2 = "a \\\\(b\\) c";
  assert.equal(findFormulaAt(doc2, at(doc2, "\\\\") + 1), null);
  assert.equal(findFormulaAt(doc2, at(doc2, "(b")), null);

  // \\ 后跟 $x$：字面反斜杠之外是真正公式，命中且不吞反斜杠
  const doc3 = "\\\\$x$";
  assert.equal(findFormulaAt(doc3, at(doc3, "$x$"))?.text, "$x$");
  assert.equal(findFormulaAt(doc3, at(doc3, "\\\\") + 1), null);

  // 悬停 \% 的 % 上不判定公式
  const doc4 = "$a$ 100\\% 好";
  assert.equal(findFormulaAt(doc4, at(doc4, "\\%")), null);
  // 转义对之外的正常公式不受影响
  assert.equal(findFormulaAt(doc4, at(doc4, "a"))?.text, "$a$");
});

test("注释行（未转义 %）内不判定公式，也不参与定界符配对", () => {
  const doc = "$x$ % 注释 $broken\ny = 1";
  assert.equal(findFormulaAt(doc, at(doc, "注释")), null);
  assert.equal(findFormulaAt(doc, at(doc, "broken")), null);
  // 注释里的 $ 不参与配对：真正的公式仍是 $x$
  const r = findFormulaAt(doc, at(doc, "x"));
  assert.equal(r.text, "$x$");
});

test("\\% 为字面百分号，其后内容仍是公式本体", () => {
  const doc = "$100\\% + 1$";
  const r = findFormulaAt(doc, 4);
  assert.equal(r.text, "$100\\% + 1$");
});

test("未闭合定界符不判定为公式", () => {
  assert.equal(findFormulaAt("$a+b 没有闭合", 3), null);
  assert.equal(findFormulaAt("\\(a+b 没有闭合", 3), null);
  assert.equal(findFormulaAt("$$a+b", 3), null);
  assert.equal(findFormulaAt("\\begin{align}\na &= b\n没有 end", 16), null);
});

test("公式外的普通文本返回 null", () => {
  const doc = "plain text, no math at all";
  assert.equal(findFormulaAt(doc, 3), null);
  assert.equal(findFormulaAt(doc, 0), null);
});

test("相邻公式各自独立命中，互不越界", () => {
  const doc = "$a$ 中间 $b$";
  const r1 = findFormulaAt(doc, 1);
  assert.equal(r1.text, "$a$");
  const r2 = findFormulaAt(doc, at(doc, "b"));
  assert.equal(r2.text, "$b$");
  assert.equal(findFormulaAt(doc, at(doc, "中间")), null);
});

test("多行 display $$ 跨行命中", () => {
  const doc = "$$\n\\int_0^1 x \\, dx = \\frac{1}{2}\n$$";
  const r = findFormulaAt(doc, at(doc, "frac"));
  assert.equal(r.from, 0);
  assert.equal(r.to, doc.length);
  assert.equal(r.display, true);
});

test("公式后的下一行公式不受前文影响", () => {
  const doc = "text $a$\n\\[b\\]";
  const r = findFormulaAt(doc, at(doc, "b"));
  assert.equal(r.text, "\\[b\\]");
});

test("输入边界：非法入参返回 null", () => {
  assert.equal(findFormulaAt(null, 0), null);
  assert.equal(findFormulaAt(undefined, 0), null);
  assert.equal(findFormulaAt("$x$", "1"), null);
  assert.equal(findFormulaAt("$x$", -1), null);
  assert.equal(findFormulaAt("$x$", 100), null);
});

test("公式区域与注释混合：注释停在行尾，公式跨行继续", () => {
  // LaTeX 语义：% 注释掉到行尾，闭口 $ 在下一行
  const doc = "$a % 备注\nb$ 尾";
  const r = findFormulaAt(doc, at(doc, "a"));
  assert.equal(r.text, "$a % 备注\nb$");
  // 注释在公式区域内时悬停仍显示所在公式（覆盖不留空洞），
  // 但注释绝不会开启新公式；公式外的注释与公式后的正文不命中
  assert.equal(findFormulaAt(doc, at(doc, "备注"))?.text, "$a % 备注\nb$");
  assert.equal(findFormulaAt(doc, at(doc, "尾")), null);
  // 纯注释行里的 $ 不开启公式
  const plain = "x % $a$\ny";
  assert.equal(findFormulaAt(plain, at(plain, "$a$")), null);
});
