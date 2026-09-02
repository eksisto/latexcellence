import test from "node:test";
import assert from "node:assert/strict";

import { EditorSelection, EditorState } from "@codemirror/state";

import {
  WRAP_CATEGORY,
  WRAP_DIRECT_SHORTCUTS,
  computeWrapEdits,
  wrapKeymapExtension,
  wrapViewSelections,
} from "../src/lib/editor/wrap.js";
import { LATEX_COMMANDS } from "../src/data/latexCommands/index.js";

/* ── 纯函数：computeWrapEdits ─────────────────────────────────────────── */

test("有选区：包裹内容并保持内容选中（便于连续叠加包裹）", () => {
  const doc = "hello world";
  const result = computeWrapEdits(doc, [{ from: 6, to: 11 }], "textbf");
  assert.deepEqual(result.changes, [
    { from: 6, to: 11, insert: "\\textbf{world}" },
  ]);
  assert.deepEqual(result.selections, [{ anchor: 14, head: 19 }]);
  // 应用到全文验证往返
  assert.equal(
    doc.slice(0, 6) + result.changes[0].insert + doc.slice(11),
    "hello \\textbf{world}",
  );
});

test("空选区（光标）：插入空命令并把光标定位到参数内", () => {
  const result = computeWrapEdits("ab", [{ from: 1, to: 1 }], "emph");
  assert.deepEqual(result.changes, [{ from: 1, to: 1, insert: "\\emph{}" }]);
  assert.deepEqual(result.selections, [{ anchor: 7, head: 7 }]);
});

test("多选区：逐个包裹且坐标随前序插入偏移", () => {
  const doc = "one two three";
  const result = computeWrapEdits(
    doc,
    [
      { from: 0, to: 3 },
      { from: 8, to: 13 },
    ],
    "underline",
  );
  assert.deepEqual(result.changes, [
    { from: 0, to: 3, insert: "\\underline{one}" },
    { from: 8, to: 13, insert: "\\underline{three}" },
  ]);
  // 第二个选区偏移 = 第一个插入的净增量（15 - 3 = 12）
  assert.deepEqual(result.selections, [
    { anchor: 11, head: 14 },
    { anchor: 31, head: 36 },
  ]);
});

test("选区方向保留：CM6 SelectionRange 的反向选区包裹后仍反向", () => {
  // CM6 SelectionRange 兼容：{from, to} 为归一区间，{anchor, head} 带方向
  const selection = EditorSelection.create([
    EditorSelection.range(3, 1), // head=3? range(anchor, head)：anchor=3, head=1 → 反向
  ]);
  const range = selection.ranges[0];
  assert.equal(range.anchor, 3);
  assert.equal(range.head, 1);
  const result = computeWrapEdits("abcd", [range], "textbf");
  // 内容 [1,3) 包成 \textbf{bc}，方向保留（head 在前）
  assert.deepEqual(result.changes, [
    { from: 1, to: 3, insert: "\\textbf{bc}" },
  ]);
  assert.deepEqual(result.selections, [{ anchor: 11, head: 9 }]);
});

test("非法输入返回 null：命令名不合法 / 区间缺失 / 区间重叠", () => {
  assert.equal(computeWrapEdits("doc", [{ from: 0, to: 1 }], "1bad"), null);
  assert.equal(computeWrapEdits("doc", [{ from: 0, to: 1 }], ""), null);
  assert.equal(computeWrapEdits("doc", [{ from: 0, to: 1 }], null), null);
  assert.equal(computeWrapEdits("doc", [], "textbf"), null);
  assert.equal(computeWrapEdits("doc", null, "textbf"), null);
  assert.equal(
    computeWrapEdits(
      "abcdef",
      [
        { from: 0, to: 3 },
        { from: 2, to: 5 },
      ],
      "textbf",
    ),
    null,
  );
});

test("区间越界被钳制到文档范围内", () => {
  const result = computeWrapEdits("hi", [{ from: 0, to: 99 }], "emph");
  assert.deepEqual(result.changes, [{ from: 0, to: 2, insert: "\\emph{hi}" }]);
});

/* ── CM6 命令层：wrapViewSelections（伪视图捕获 dispatch） ────────────── */

function makeFakeView(docText, ranges, { readOnly = false } = {}) {
  const dispatched = [];
  return {
    state: {
      doc: { toString: () => docText },
      selection: { ranges },
      readOnly,
    },
    destroyed: false,
    focus() {},
    dispatch(spec) {
      dispatched.push(spec);
    },
    dispatched,
  };
}

test("wrapViewSelections dispatch 包裹编辑并返回 true", () => {
  const view = makeFakeView("hello", [{ from: 0, to: 5 }]);
  assert.equal(wrapViewSelections(view, "texttt"), true);
  assert.equal(view.dispatched.length, 1);
  const spec = view.dispatched[0];
  assert.deepEqual(spec.changes, [
    { from: 0, to: 5, insert: "\\texttt{hello}" },
  ]);
  assert.equal(spec.selection.ranges.length, 1);
  assert.equal(spec.scrollIntoView, true);
});

test("wrapViewSelections 只读 / 无视图 / 非法命令返回 false 且不 dispatch", () => {
  const roView = makeFakeView("hello", [{ from: 0, to: 5 }], {
    readOnly: true,
  });
  assert.equal(wrapViewSelections(roView, "textbf"), false);
  assert.equal(roView.dispatched.length, 0);

  assert.equal(wrapViewSelections(null, "textbf"), false);
  const destroyed = makeFakeView("hello", [{ from: 0, to: 5 }]);
  destroyed.destroyed = true;
  assert.equal(wrapViewSelections(destroyed, "textbf"), false);

  const badView = makeFakeView("hello", [{ from: 0, to: 5 }]);
  assert.equal(wrapViewSelections(badView, "bad name"), false);
  assert.equal(badView.dispatched.length, 0);
});

/* ── 快捷键映射与命令数据库一致性 ─────────────────────────────────────── */

test("直包快捷键与 wrap 类目命令一一对应", () => {
  assert.deepEqual(
    WRAP_DIRECT_SHORTCUTS.map((s) => s.command),
    ["textbf", "emph", "underline", "texttt"],
  );
  const byName = new Map(LATEX_COMMANDS.map((e) => [e.name, e]));
  for (const { command } of WRAP_DIRECT_SHORTCUTS) {
    const entry = byName.get(command);
    assert.ok(entry, `${command} 应在命令数据库中`);
    assert.equal(entry.category, WRAP_CATEGORY);
  }
});

test("键映射扩展可构造", () => {
  // keymap.of 返回的 Extension 不可直接枚举键位；此处仅验证可构造且
  // 不抛错（键位行为由 CM6 集成层与纯函数测试共同覆盖）
  assert.ok(wrapKeymapExtension());
});

/* ── 端到端：真实 EditorSelection 输入 + 变更可被 EditorState 应用 ────── */

test("多选区包裹结果可应用到真实 EditorState", () => {
  const selection = EditorSelection.create([
    EditorSelection.range(0, 3),
    EditorSelection.range(8, 13),
  ]);
  const result = computeWrapEdits("one two three", selection.ranges, "emph");
  const applied = EditorState.create({
    doc: "one two three",
    // 多选区选区集仅在 allowMultipleSelections 开启时保留
    extensions: [EditorState.allowMultipleSelections.of(true)],
  }).update({
    changes: result.changes,
    selection: EditorSelection.create(
      result.selections.map((s) => EditorSelection.range(s.anchor, s.head)),
    ),
  });
  assert.equal(applied.state.doc.toString(), "\\emph{one} two \\emph{three}");
  // 每个选区落在对应包裹内容上（内容保持选中）
  const heads = applied.selection.ranges.map((r) =>
    applied.state.doc.sliceString(r.from, r.to),
  );
  assert.deepEqual(heads, ["one", "three"]);
});
