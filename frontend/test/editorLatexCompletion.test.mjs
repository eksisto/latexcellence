/**
 * latexCompletion 双 source 的应用区间契约（node --test，无 DOM）：
 * 以 CompletionContext 直接驱动 source，再用极简 mock view 走 apply 回调，
 * 断言真实派发的替换区间与插入文本。
 *
 * 关键回归：命令补全的 result.from 在手敲 `\` 之后，而 insert 模板自带
 * 前导 `\`，apply 若不左移一格吞掉已敲入的 `\`，应用后得到 `\\alpha`。
 */

import test from "node:test";
import assert from "node:assert/strict";

import { EditorState } from "@codemirror/state";
import { CompletionContext } from "@codemirror/autocomplete";

import {
  extractEnvironments,
  makeCommandCompletionSource,
  makeEnvironmentCompletionSource,
} from "../src/lib/editor/latexCompletion.js";

/** 极简 EditorView 替身：记录 dispatch 的 spec，doc 为纯字符串切片 */
function makeMockView(docText) {
  const dispatched = [];
  const view = {
    state: {
      doc: { sliceString: (from, to) => docText.slice(from, to) },
      selection: { main: { from: docText.length, to: docText.length } },
    },
    dispatch: (spec) => dispatched.push(spec),
    focus() {},
  };
  return { view, dispatched };
}

/** 在文档末尾显式触发一次补全查询 */
function runSource(source, docText) {
  const state = EditorState.create({ doc: docText });
  return source(new CompletionContext(state, docText.length, true));
}

test("命令补全：\\al 应用 alpha 后为单反斜杠（替换区间吞掉手敲的 \\）", async () => {
  const source = makeCommandCompletionSource(undefined, () => "zh");
  const doc = "\\al";
  const result = await runSource(source, doc);
  assert.ok(result, "应产生补全结果");
  assert.equal(result.from, 1, "source 返回的 from 应在反斜杠之后");
  const option = result.options.find((o) => o.label === "alpha");
  assert.ok(option, "候选中应含 alpha");

  const { view, dispatched } = makeMockView(doc);
  option.apply(view, option, result.from, doc.length);
  assert.equal(dispatched.length, 1, "apply 应恰好派发一次事务");
  assert.deepEqual(dispatched[0].changes, {
    from: 0,
    to: doc.length,
    insert: "\\alpha",
  });
});

test("命令补全：刚敲下 \\ 的空查询应用后同样只留单反斜杠", async () => {
  const source = makeCommandCompletionSource(undefined, () => "zh");
  const doc = "\\";
  const result = await runSource(source, doc);
  assert.ok(result, "空查询应返回默认列表");
  const option = result.options.find((o) => o.label === "alpha");
  assert.ok(option, "默认列表应含 greek 首条 alpha");

  const { view, dispatched } = makeMockView(doc);
  option.apply(view, option, result.from, doc.length);
  assert.deepEqual(dispatched[0].changes, {
    from: 0,
    to: 1,
    insert: "\\alpha",
  });
});

test("命令补全：区间前一字符不是 \\ 时回退原区间（防御路径）", async () => {
  const source = makeCommandCompletionSource(undefined, () => "zh");
  const doc = "\\al";
  const result = await runSource(source, doc);
  const option = result.options.find((o) => o.label === "alpha");

  // 弹层存续期间手敲的 \ 被删除：from2 前不再是 \，不得越界左移
  const { view, dispatched } = makeMockView("xal");
  option.apply(view, option, 1, 3);
  assert.deepEqual(dispatched[0].changes, {
    from: 1,
    to: 3,
    insert: "\\alpha",
  });
});

test("环境补全：替换区间覆盖 \\begin{ 前缀，模板整体替换", async () => {
  const source = makeEnvironmentCompletionSource(undefined, () => "zh");
  const doc = "\\begin{al";
  const result = await runSource(source, doc);
  assert.ok(result, "应产生补全结果");
  assert.equal(result.from, 0, "替换起点应覆盖 \\begin{");
  const option = result.options.find((o) => o.label === "align");
  assert.ok(option, "候选中应含 align");

  const { view, dispatched } = makeMockView(doc);
  option.apply(view, option, result.from, doc.length);
  const changes = dispatched[0].changes;
  assert.equal(changes.from, 0);
  assert.equal(changes.to, doc.length);
  assert.ok(changes.insert.startsWith("\\begin{align}"));
  assert.ok(changes.insert.includes("\\end{align}"), "应含自动配对的 \\end");
});

test("环境清单：extractEnvironments 覆盖 align 且名称去重", () => {
  const envs = extractEnvironments();
  const align = envs.find((e) => e.name === "align");
  assert.ok(align, "应从数据条目提取出 align 环境");
  assert.equal(
    envs.filter((e) => e.name === "align").length,
    1,
    "同名环境应去重",
  );
});
