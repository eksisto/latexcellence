import test from "node:test";
import assert from "node:assert/strict";

import { EditorSelection, EditorState } from "@codemirror/state";
import { history, undo } from "@codemirror/commands";

import {
  autoCloseEnvExtension,
  beginAutoCloseDecision,
} from "../src/lib/editor/beginClose.js";

/* ── 纯函数：beginAutoCloseDecision ───────────────────────────────────── */

test("光标前文本以 \\begin{env} 结尾且后方无 \\end：返回补全决策", () => {
  const d = beginAutoCloseDecision("  \\begin{itemize}", "");
  assert.deepEqual(d, {
    env: "itemize",
    insert: "\n\n  \\end{itemize}",
    cursorOffset: 1,
  });
});

test("插入文本继承 \\begin 所在行的行首缩进", () => {
  const d = beginAutoCloseDecision("\t\\begin{center}", "rest");
  assert.equal(d.insert, "\n\n\t\\end{center}");
});

test("光标后已有对应 \\end{env}（曾配对）：不再补", () => {
  assert.equal(beginAutoCloseDecision("\\begin{a}", " x \\end{a}"), null);
  // 不同环境名的 \end 不算已配对
  const d = beginAutoCloseDecision("\\begin{a}", "\\end{b}");
  assert.ok(d);
  assert.equal(d.env, "a");
});

test("嵌套同名环境：后方完整同名配对被跳过，本 \\begin 未配对仍补", () => {
  // 光标后的 \end 配对的是嵌套层的 \begin，不是本 \begin → 仍补
  const d = beginAutoCloseDecision(
    "\\begin{itemize}",
    " tail \\begin{itemize} x \\end{itemize} more",
  );
  assert.ok(d);
  assert.equal(d.env, "itemize");
  // 嵌套层被两个 \begin 抵消后仍剩一个 \end → 本 \begin 已配对 → 不补
  assert.equal(
    beginAutoCloseDecision(
      "\\begin{a}",
      " \\begin{a} \\begin{a} \\end{a} \\end{a} \\end{a}",
    ),
    null,
  );
  // 同名 \end 在嵌套 \begin 之前直接出现 → 本 \begin 已配对 → 不补
  assert.equal(beginAutoCloseDecision("\\begin{a}", " \\end{a}"), null);
});

test("注释行内 \\end 不参与已配对判断", () => {
  const d = beginAutoCloseDecision("\\begin{a}", " % \\end{a}\nrest");
  assert.ok(d);
  assert.equal(d.env, "a");
});

test("% 注释行内的 \\begin 不触发补全", () => {
  assert.equal(beginAutoCloseDecision("% \\begin{a}", ""), null);
  assert.equal(beginAutoCloseDecision("  % \\begin{a}", ""), null);
  // \begin 前的未转义 % 在同一行即可（哪怕隔了文本）
  assert.equal(beginAutoCloseDecision("note % \\begin{a}", ""), null);
  // 转义 \% 为字面百分号，不算注释 → 仍触发
  const d = beginAutoCloseDecision("50\\% \\begin{a}", "");
  assert.ok(d);
  assert.equal(d.env, "a");
  // 注释只在行内生效：前一行是注释不影响本行
  const d2 = beginAutoCloseDecision("% old\n\\begin{a}", "");
  assert.ok(d2);
});

test("光标前文本非 \\begin 结尾：返回 null", () => {
  assert.equal(beginAutoCloseDecision("hello}", ""), null);
  assert.equal(beginAutoCloseDecision("\\begin{}", ""), null);
  assert.equal(beginAutoCloseDecision("x\\begin{a", ""), null);
  assert.equal(beginAutoCloseDecision("", ""), null);
  assert.equal(beginAutoCloseDecision("\\end{a}", ""), null);
});

test("环境名允许字母 / 数字 / 星号（align* 等带星形态）", () => {
  const d = beginAutoCloseDecision("\\begin{align*}", "");
  assert.equal(d.env, "align*");
  const d2 = beginAutoCloseDecision("\\begin{enumerate2}", "");
  assert.equal(d2.env, "enumerate2");
});

test("非法入参返回 null", () => {
  assert.equal(beginAutoCloseDecision(null, ""), null);
  assert.equal(beginAutoCloseDecision("\\begin{a}", 42), null);
});

/* ── CM6 层：autoCloseEnvExtension（真实 EditorState 事务） ───────────── */

const MULTI = EditorState.allowMultipleSelections.of(true);

function update(state, spec) {
  return state.update(spec);
}

test("敲 } 完成 \\begin{itemize}：自动插入 \\n\\n\\end 并把光标留在中间行", () => {
  const state = EditorState.create({
    doc: "  \\begin{itemize",
    extensions: [autoCloseEnvExtension()],
  });
  const tr = update(state, {
    changes: { from: 16, to: 16, insert: "}" },
    selection: { anchor: 17 },
    userEvent: "input.type",
  });
  assert.equal(
    tr.state.doc.toString(),
    "  \\begin{itemize}\n\n  \\end{itemize}",
  );
  // 光标 = 插入点 + 1（第一个换行之后、中间空行行首）
  assert.equal(tr.state.selection.main.head, 17 + 1);
});

test("后方已有对应 \\end：敲 } 不补", () => {
  const state = EditorState.create({
    doc: "\\begin{a x \\end{a}",
    extensions: [autoCloseEnvExtension()],
  });
  const tr = update(state, {
    changes: { from: 8, to: 8, insert: "}" },
    selection: { anchor: 9 },
    userEvent: "input.type",
  });
  assert.equal(tr.state.doc.toString(), "\\begin{a} x \\end{a}");
});

test("autoCloseEnv 开关关闭：不补", () => {
  const state = EditorState.create({
    doc: "\\begin{x",
    extensions: [autoCloseEnvExtension({ isEnabled: () => false })],
  });
  const tr = update(state, {
    changes: { from: 8, to: 8, insert: "}" },
    selection: { anchor: 9 },
    userEvent: "input.type",
  });
  assert.equal(tr.state.doc.toString(), "\\begin{x}");
});

test("只读状态不补", () => {
  const state = EditorState.create({
    doc: "\\begin{x",
    extensions: [autoCloseEnvExtension(), EditorState.readOnly.of(true)],
  });
  const tr = update(state, {
    changes: { from: 8, to: 8, insert: "}" },
    selection: { anchor: 9 },
    userEvent: "input.type",
  });
  assert.equal(tr.state.doc.toString(), "\\begin{x}");
});

test("删除事务把光标落到 } 后不触发（非 input 插入）", () => {
  const state = EditorState.create({
    doc: "x\\begin{a}",
    extensions: [autoCloseEnvExtension()],
  });
  const tr = update(state, {
    changes: { from: 0, to: 1, insert: "" },
    selection: { anchor: 0 },
    userEvent: "delete.backward",
  });
  assert.equal(tr.state.doc.toString(), "\\begin{a}");
});

test("普通文本（含 }）不触发", () => {
  const state = EditorState.create({
    doc: "hello",
    extensions: [autoCloseEnvExtension()],
  });
  const tr = update(state, {
    changes: { from: 5, to: 5, insert: "}" },
    selection: { anchor: 6 },
    userEvent: "input.type",
  });
  assert.equal(tr.state.doc.toString(), "hello}");
});

test("\\end{... 的 } 不触发（尾匹配只认 \\begin）", () => {
  const state = EditorState.create({
    doc: "\\begin{a}\nX\n\\end{a",
    extensions: [autoCloseEnvExtension()],
  });
  const tr = update(state, {
    changes: { from: 18, to: 18, insert: "}" },
    selection: { anchor: 19 },
    userEvent: "input.type",
  });
  assert.equal(tr.state.doc.toString(), "\\begin{a}\nX\n\\end{a}");
});

test("多光标：每个落在插入末尾的光标各自补 \\end", () => {
  const state = EditorState.create({
    doc: "\\begin{a\n\\begin{b",
    extensions: [autoCloseEnvExtension(), MULTI],
  });
  const tr = update(state, {
    changes: [
      { from: 8, to: 8, insert: "}" },
      { from: 17, to: 17, insert: "}" },
    ],
    selection: EditorSelection.create([
      EditorSelection.cursor(9),
      EditorSelection.cursor(19),
    ]),
    userEvent: "input.type",
  });
  assert.equal(
    tr.state.doc.toString(),
    "\\begin{a}\n\n\\end{a}\n\\begin{b}\n\n\\end{b}",
  );
  const heads = tr.state.selection.ranges.map((r) => r.head);
  // 两个光标分别落在两个中间空行行首
  assert.equal(heads.length, 2);
  assert.deepEqual(
    heads.map((h) => tr.state.doc.lineAt(h).text),
    ["", ""],
  );
});

test("补全事务与打字事务合并为单事务（Ctrl+Z 一步撤销）", () => {
  const state = EditorState.create({
    doc: "\\begin{x",
    extensions: [autoCloseEnvExtension()],
  });
  const tr = update(state, {
    changes: { from: 8, to: 8, insert: "}" },
    selection: { anchor: 9 },
    userEvent: "input.type",
  });
  // 合并后整体仍是同一事务（变更 + 选区同时生效）
  assert.equal(tr.state.doc.toString(), "\\begin{x}\n\n\\end{x}");
  assert.equal(tr.state.selection.main.head, 10);
});

test("带 history 扩展：Ctrl+Z 一步撤销打字 + 补全", () => {
  const state = EditorState.create({
    doc: "\\begin{x",
    extensions: [autoCloseEnvExtension(), history()],
  });
  const tr = update(state, {
    changes: { from: 8, to: 8, insert: "}" },
    selection: { anchor: 9 },
    userEvent: "input.type",
  });
  assert.equal(tr.state.doc.toString(), "\\begin{x}\n\n\\end{x}");
  // 单步 undo 回到打字前（补全不会残留为第二步）
  const fake = {
    state: tr.state,
    dispatch(transaction) {
      fake.state = transaction.state;
    },
  };
  assert.equal(undo(fake), true);
  assert.equal(fake.state.doc.toString(), "\\begin{x");
});

test("注释行内的 \\begin 敲 } 不补（CM6 事务级回归）", () => {
  const state = EditorState.create({
    doc: "text % \\begin{x",
    extensions: [autoCloseEnvExtension()],
  });
  const tr = update(state, {
    changes: { from: 15, to: 15, insert: "}" },
    selection: { anchor: 16 },
    userEvent: "input.type",
  });
  assert.equal(tr.state.doc.toString(), "text % \\begin{x}");
});

test("嵌套同名环境已配对时敲 } 不补（CM6 事务级回归）", () => {
  // 已有完整同名环境；在其前方新敲一个 \begin{a}——
  // 修复前 naive includes 会把嵌套层的 \end 误判为本 \begin 已配对
  const state = EditorState.create({
    doc: " \\begin{a} x \\end{a}",
    extensions: [autoCloseEnvExtension()],
  });
  const tr = update(state, {
    changes: { from: 0, to: 0, insert: "\\begin{a" },
    selection: { anchor: 8 },
    userEvent: "input.type",
  });
  const tr2 = update(tr.state, {
    changes: { from: 8, to: 8, insert: "}" },
    selection: { anchor: 9 },
    userEvent: "input.type",
  });
  // 新 \begin{a} 未配对（后方的 \end 配对的是嵌套层）→ 应补
  assert.equal(
    tr2.state.doc.toString(),
    "\\begin{a}\n\n\\end{a} \\begin{a} x \\end{a}",
  );
  // 对照：新 \begin{a} 紧邻一个可直接配对的 \end → 不补
  const state2 = EditorState.create({
    doc: " \\end{a}",
    extensions: [autoCloseEnvExtension()],
  });
  const t1 = update(state2, {
    changes: { from: 0, to: 0, insert: "\\begin{a" },
    selection: { anchor: 8 },
    userEvent: "input.type",
  });
  const t2 = update(t1.state, {
    changes: { from: 8, to: 8, insert: "}" },
    selection: { anchor: 9 },
    userEvent: "input.type",
  });
  assert.equal(t2.state.doc.toString(), "\\begin{a} \\end{a}");
});
