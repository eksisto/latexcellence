import test from "node:test";
import assert from "node:assert/strict";

import { EditorState } from "@codemirror/state";

import {
  buildGridCells,
  buildGridColSpec,
  expandEntryInsert,
  exitPlaceholderMode,
  GRID_CELL_MARKER,
  insertSnippet,
  isGridTemplate,
  nextPlaceholder,
  nextPlaceholderNumber,
  parsePlaceholders,
  placeholderActive,
  placeholderRanges,
  previousPlaceholder,
  renderGridTemplate,
  renderSnippet,
  snippetPlaceholderExtension,
} from "../src/lib/editor/snippetInsert.js";
import { LATEX_COMMANDS } from "../src/data/latexCommands/index.js";

test("parsePlaceholders：按编号升序返回位置，$0 一并收录", () => {
  const marks = parsePlaceholders("\\frac{$1}{$2}$0");
  assert.deepEqual(
    marks.map((m) => m.num),
    [0, 1, 2],
  );
  // $1 起始位置在 \frac{ 之后（6 个字符）
  assert.equal(marks.find((m) => m.num === 1).start, 6);
});

test("renderSnippet：剥离标记并给出停泊区间与退出点", () => {
  const { text, ranges, exit } = renderSnippet("\\frac{$1}{$2}$0");
  assert.equal(text, "\\frac{}{}");
  assert.deepEqual(
    ranges.map((r) => [r.from, r.to]),
    [
      [6, 6],
      [8, 8],
    ],
  );
  assert.equal(exit, 9);
});

test("renderSnippet：无占位符模板原样保留；$ 后非数字视为字面字符", () => {
  assert.deepEqual(renderSnippet("\\alpha"), {
    text: "\\alpha",
    ranges: [],
    exit: null,
  });
  // 约定：$ 后跟数字即为占位符（多位数编号供网格单元格使用），
  // 字面美元符在 LaTeX 中本应写作 \\$，故 $x 这类形式保持字面
  const literal = renderSnippet("成本 $x 与 $y");
  assert.equal(literal.text, "成本 $x 与 $y");
  assert.ok(literal.ranges.length === 0);
});

test("nextPlaceholderNumber：接续模板已有编号", () => {
  assert.equal(nextPlaceholderNumber("\\caption{$1}"), 2);
  assert.equal(nextPlaceholderNumber("no marks"), 1);
  assert.equal(nextPlaceholderNumber("$3 and $7"), 8);
});

test("buildGridCells：行优先连续编号、& 列分隔、\\\\ 行分隔", () => {
  assert.equal(buildGridCells(2, 3), "$1 & $2 & $3 \\\\\n\t$4 & $5 & $6");
  assert.equal(buildGridCells(1, 1, 5), "$5");
});

test("buildGridColSpec / isGridTemplate", () => {
  assert.equal(buildGridColSpec(3), "ccc");
  assert.ok(isGridTemplate(`\\begin{x}\n${GRID_CELL_MARKER}\n\\end{x}`));
  assert.equal(isGridTemplate("\\alpha"), false);
});

test("网格骨架行列正确性：tabular 2×3 展开为 ccc + 6 个单元格占位符", () => {
  const entry = LATEX_COMMANDS.find((e) => e.name === "tabular");
  const template = renderGridTemplate(entry.insert, 2, 3);
  assert.ok(template.includes("{ccc}"));
  assert.ok(!template.includes("%COLS%") && !template.includes("%CELLS%"));
  const { ranges } = renderSnippet(template);
  assert.equal(ranges.length, 6);
  assert.deepEqual(
    ranges.map((r) => r.num),
    [1, 2, 3, 4, 5, 6],
  );
  // 两行之间恰好一处 LaTeX 行分隔符 \\
  assert.ok(template.split("\\\\").length === 2);
});

test("三线表：表题占用 $1，单元格从 $2 接续且数量=行列乘积", () => {
  const entry = LATEX_COMMANDS.find((e) => e.name === "threelinetable");
  const rendered = expandEntryInsert(entry, { rows: 2, cols: 4 });
  const { ranges, exit } = renderSnippet(rendered);
  assert.equal(ranges.length, 1 + 2 * 4); // 表题 + 单元格
  assert.deepEqual(
    ranges.map((r) => r.num),
    [1, 2, 3, 4, 5, 6, 7, 8, 9],
  );
  assert.notEqual(exit, null);
  assert.ok(
    rendered.includes("\\toprule") && rendered.includes("\\bottomrule"),
  );
  assert.ok(rendered.includes("{cccc}"));
});

test("expandEntryInsert：非网格原样返回；缺省尺寸用条目默认 grid", () => {
  const plain = LATEX_COMMANDS.find((e) => e.name === "frac");
  assert.equal(expandEntryInsert(plain), plain.insert);

  const matrix = LATEX_COMMANDS.find((e) => e.name === "pmatrix");
  const defaulted = expandEntryInsert(matrix, null);
  assert.ok(
    defaulted.includes("\\begin{pmatrix}") &&
      defaulted.includes("\\end{pmatrix}"),
  );
  assert.equal(renderSnippet(defaulted).ranges.length, 4); // 默认 2×2
});

/* ── CM6 占位符状态机（StateField 事务级行为，node 环境直接驱动） ─────── */

/** 无头视图桩：dispatch 直接经 EditorState.update 应用事务 */
function makeView(initialDoc) {
  let state = EditorState.create({
    doc: initialDoc,
    extensions: [snippetPlaceholderExtension()],
  });
  return {
    get state() {
      return state;
    },
    dispatch(spec) {
      state = state.update(spec).state;
    },
    focus() {},
  };
}

test("insertSnippet 进入占位符模式；占位符内输入随映射扩张", () => {
  const view = makeView("begin");
  assert.ok(insertSnippet(view, "\\frac{$1}{$2}$0", { from: 5, to: 5 }));
  assert.ok(placeholderActive(view.state));
  assert.equal(view.state.doc.toString(), "begin\\frac{}{}");
  // 两个空停泊点位于渲染文本内（相对插入点偏移 6 / 8）
  assert.deepEqual(placeholderRanges(view.state), [
    { from: 11, to: 11 },
    { from: 13, to: 13 },
  ]);

  // 在 $1 内输入 x：from 左结合、to 右结合 → 区间扩张覆盖输入内容
  view.dispatch({
    changes: { from: 11, to: 11, insert: "x" },
    userEvent: "input.type",
  });
  assert.ok(placeholderActive(view.state));
  const ranges = placeholderRanges(view.state);
  assert.deepEqual(ranges[0], { from: 11, to: 12 });
  assert.deepEqual(ranges[1], { from: 14, to: 14 }); // $2 随之右移
});

test("普通编辑事务保持模式；undo 事务退出占位符模式（history 互操作）", () => {
  const view = makeView("");
  insertSnippet(view, "\\frac{$1}{$2}$0");
  assert.ok(placeholderActive(view.state));

  // 普通输入不清模式
  view.dispatch({ changes: { from: 6, insert: "a" }, userEvent: "input.type" });
  assert.ok(placeholderActive(view.state));

  // 整体撤销插入：模式必须清除，而非残留空停泊点劫持 Tab/Esc
  const lenBeforeUndo = view.state.doc.length;
  view.dispatch({
    changes: { from: 0, to: lenBeforeUndo, insert: "" },
    userEvent: "undo",
  });
  assert.equal(view.state.doc.toString(), "");
  assert.equal(placeholderActive(view.state), false);
});

test("Tab 推进停泊点、Shift+Tab 反向回绕、Esc 退出；未激活时不劫持按键", () => {
  const view = makeView("");
  insertSnippet(view, "$1-$2-$3$0");
  assert.equal(view.state.doc.toString(), "--");
  assert.equal(view.state.selection.main.from, 0);

  assert.ok(nextPlaceholder(view)); // → $2
  assert.equal(view.state.selection.main.from, 1);
  assert.ok(previousPlaceholder(view)); // 回绕 → $1
  assert.equal(view.state.selection.main.from, 0);
  assert.ok(previousPlaceholder(view)); // 反向回绕 → $3
  assert.equal(view.state.selection.main.from, 2);
  assert.ok(nextPlaceholder(view)); // → $0 退站点并结束模式
  assert.equal(view.state.selection.main.from, 2);
  assert.equal(placeholderActive(view.state), false);

  // 未激活时返回 false，交还默认键位（如缩进）
  const idle = makeView("abc");
  assert.equal(nextPlaceholder(idle), false);
  assert.equal(previousPlaceholder(idle), false);
  assert.equal(exitPlaceholderMode(idle), false);
});
