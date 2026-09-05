import test from "node:test";
import assert from "node:assert/strict";

import { groupProjectOutline } from "../src/lib/editor/outlineProject.js";
import { lineColToOffset } from "../src/lib/editor/formulaHoverScan.js";

test("groupProjectOutline：按文件分组，激活文件置顶", () => {
  const files = [
    { file_path: "main.tex", entries: [{ level: 0, title: "Intro", line: 1 }] },
    {
      file_path: "chapters/method.tex",
      entries: [{ level: 1, title: "Method", line: 2 }],
    },
    {
      file_path: "appendix.tex",
      entries: [{ level: 0, title: "Appendix", line: 5 }],
    },
  ];
  const groups = groupProjectOutline(files, "appendix.tex");
  assert.deepEqual(
    groups.map((g) => [g.file_path, g.active]),
    [
      ["appendix.tex", true],
      ["chapters/method.tex", false],
      ["main.tex", false],
    ],
  );
});

test("groupProjectOutline：其余组按 file_path 字典序（与后端 rglob 排序一致）", () => {
  const files = [
    { file_path: "b/2.tex", entries: [{ level: 0, title: "b2", line: 1 }] },
    { file_path: "a/1.tex", entries: [{ level: 0, title: "a1", line: 1 }] },
    { file_path: "c.tex", entries: [{ level: 0, title: "c", line: 1 }] },
  ].reverse();
  const groups = groupProjectOutline(files, null);
  assert.deepEqual(
    groups.map((g) => g.file_path),
    ["a/1.tex", "b/2.tex", "c.tex"],
  );
});

test("groupProjectOutline：空条目组剔除，脏输入不抛错", () => {
  const groups = groupProjectOutline(
    [
      { file_path: "empty.tex", entries: [] },
      { file_path: "main.tex", entries: [{ level: 0, title: "A", line: 1 }] },
      { file_path: "no-entries.tex" },
    ],
    "empty.tex",
  );
  assert.deepEqual(
    groups.map((g) => g.file_path),
    ["main.tex"],
  );
  assert.deepEqual(groupProjectOutline(null, null), []);
  assert.deepEqual(groupProjectOutline(undefined, "x"), []);
  assert.deepEqual(groupProjectOutline("not-an-array", "x"), []);
});

test("groupProjectOutline：同一文件不重复、激活组唯一", () => {
  const files = [
    { file_path: "main.tex", entries: [{ level: 0, title: "A", line: 1 }] },
  ];
  const groups = groupProjectOutline(files, "main.tex");
  assert.equal(groups.length, 1);
  assert.equal(groups[0].active, true);
});

test("lineColToOffset：1 基行号列号 → 文档偏移量", () => {
  const doc = "ab\ncdef\n\ng";
  // 第 1 行第 1 列 → 0
  assert.equal(lineColToOffset(doc, 1, 1), 0);
  // 第 2 行第 3 列 → 3 + 2 = 5
  assert.equal(lineColToOffset(doc, 2, 3), 5);
  // 空行首列："ab\n" + "cdef\n" = 8
  assert.equal(lineColToOffset(doc, 3, 1), 8);
  // 最后一行（无换行结尾）："ab\n" + "cdef\n" + "\n" = 9；col 2 钳到行尾
  assert.equal(lineColToOffset(doc, 4, 1), 9);
  assert.equal(lineColToOffset(doc, 4, 2), 10);
});

test("lineColToOffset：col 超行钳到行尾，line 超文档返回 -1", () => {
  const doc = "ab\ncd";
  assert.equal(lineColToOffset(doc, 1, 99), 2); // 钳到第 1 行行尾（"ab" 长 2）
  assert.equal(lineColToOffset(doc, 99, 1), -1); // 行号越界
  assert.equal(lineColToOffset("", 1, 1), -1); // 空文档
});

test("lineColToOffset：非法输入回落 1 基原点", () => {
  const doc = "abc";
  assert.equal(lineColToOffset(doc, undefined, undefined), 0);
  assert.equal(lineColToOffset(doc, 0, 0), 0);
  assert.equal(lineColToOffset(doc, -1, -5), 0);
  assert.equal(lineColToOffset(null, 1, 1), -1);
});

test("lineColToOffset 与 findFormulaAt 串联：光标快照命中所在公式", async () => {
  const { findFormulaAt } =
    await import("../src/lib/editor/formulaHoverScan.js");
  const doc = "text $x^2$ more";
  // 光标落在第 1 行第 9 列（公式内部的 ^ 处）
  const pos = lineColToOffset(doc, 1, 9);
  const region = findFormulaAt(doc, pos);
  assert.ok(region, "cursor inside formula should hit");
  assert.equal(region.text, "$x^2$");
  assert.equal(region.display, false);
});
