import test from "node:test";
import assert from "node:assert/strict";

import { EditorState } from "@codemirror/state";

import {
  changeHistoryField,
  changeHistoryExtension,
  getChangeBaselines,
  setOpenedTextEffect,
  setSavedTextEffect,
} from "../src/lib/editor/changeHistory.js";

function makeState(doc) {
  return EditorState.create({
    doc,
    extensions: [...changeHistoryExtension()],
  });
}

function fieldOf(state) {
  return state.field(changeHistoryField);
}

test("初始字段：无基线、集合为空", () => {
  const state = makeState("a\nb\nc");
  const field = fieldOf(state);
  assert.equal(field.savedText, null);
  assert.equal(field.openedText, null);
  assert.equal(field.unsaved.size, 0);
  assert.equal(field.saved.size, 0);
  assert.deepEqual(getChangeBaselines(state), {
    savedText: null,
    openedText: null,
  });
});

test("注入基线后编辑：橙标命中修改行，绿标为空", () => {
  let state = makeState("a\nb\nc");
  state = state.update({
    effects: [
      setSavedTextEffect.of("a\nb\nc"),
      setOpenedTextEffect.of("a\nb\nc"),
    ],
  }).state;
  assert.equal(fieldOf(state).unsaved.size, 0);

  // 键入：第 2 行改为 B → 橙 {2}
  state = state.update({ changes: { from: 2, to: 3, insert: "B" } }).state;
  const field = fieldOf(state);
  assert.deepEqual([...field.unsaved].sort(), [2]);
  assert.equal(field.saved.size, 0);
  // 基线随 props 保持在效果注入值（编辑不影响基线文本）
  assert.deepEqual(getChangeBaselines(state), {
    savedText: "a\nb\nc",
    openedText: "a\nb\nc",
  });
});

test("保存回写（saved 基线推进）：橙清零，绿标本会话已保存修改", () => {
  let state = makeState("a\nb\nc");
  state = state.update({
    effects: [
      setSavedTextEffect.of("a\nb\nc"),
      setOpenedTextEffect.of("a\nb\nc"),
    ],
  }).state;
  state = state.update({ changes: { from: 2, to: 3, insert: "B" } }).state;

  // 保存成功：savedBuffer ← "a\nB\nc"（绿：相对打开基线改了第 2 行）
  state = state.update({ effects: [setSavedTextEffect.of("a\nB\nc")] }).state;
  const field = fieldOf(state);
  assert.equal(field.unsaved.size, 0);
  assert.deepEqual([...field.saved].sort(), [2]);
});

test("绿标冻结模型：编辑已保存行转橙，撤回后恢复绿", () => {
  let state = makeState("a\nb\nc");
  state = state.update({
    effects: [
      setSavedTextEffect.of("a\nb\nc"),
      setOpenedTextEffect.of("a\nb\nc"),
    ],
  }).state;
  // 改第 2 行并保存 → 绿 {2}
  state = state.update({ changes: { from: 2, to: 3, insert: "B" } }).state;
  state = state.update({ effects: [setSavedTextEffect.of("a\nB\nc")] }).state;
  assert.deepEqual([...fieldOf(state).saved].sort(), [2]);

  // 再次编辑第 2 行 → 橙 {2}，绿被扣除
  state = state.update({ changes: { from: 2, to: 3, insert: "C" } }).state;
  let field = fieldOf(state);
  assert.deepEqual([...field.unsaved].sort(), [2]);
  assert.equal(field.saved.size, 0);

  // 撤回（整文档替换回已保存内容）→ 橙清零、冻结绿恢复
  state = state.update({
    changes: { from: 0, to: state.doc.length, insert: "a\nB\nc" },
  }).state;
  field = fieldOf(state);
  assert.equal(field.unsaved.size, 0);
  assert.deepEqual([...field.saved].sort(), [2]);
});

test("打开基线注入（显式重拉）：全部集合清零", () => {
  let state = makeState("a\nB\nc");
  state = state.update({
    effects: [
      setSavedTextEffect.of("a\nb\nc"),
      setOpenedTextEffect.of("a\nb\nc"),
    ],
  }).state;
  assert.deepEqual([...fieldOf(state).unsaved].sort(), [2]);

  // 冲突放弃重载：buffer/saved/opened 全部回到磁盘内容
  state = state.update({
    changes: { from: 0, to: state.doc.length, insert: "a\nb\nc" },
    effects: [
      setSavedTextEffect.of("a\nb\nc"),
      setOpenedTextEffect.of("a\nb\nc"),
    ],
  }).state;
  const field = fieldOf(state);
  assert.equal(field.unsaved.size, 0);
  assert.equal(field.saved.size, 0);
});

test("saved 基线为 null：不产生橙标（恢复降级路径）", () => {
  let state = makeState("a\nb\nc");
  state = state.update({ effects: [setSavedTextEffect.of(null)] }).state;
  state = state.update({ changes: { from: 2, to: 3, insert: "B" } }).state;
  const field = fieldOf(state);
  assert.equal(field.unsaved.size, 0);
  assert.equal(field.saved.size, 0);
});

test("无效果且无 doc 变更的事务：字段引用不变（零装饰抖动）", () => {
  let state = makeState("a\nb");
  state = state.update({
    effects: [setSavedTextEffect.of("a\nb")],
  }).state;
  const before = fieldOf(state);
  state = state.update({ selection: { anchor: 1 } }).state;
  assert.equal(fieldOf(state), before);
});

test("绿集越界行在删行后过滤", () => {
  let state = makeState("a\nb\nc");
  state = state.update({
    effects: [
      setSavedTextEffect.of("a\nb\nc"),
      setOpenedTextEffect.of("a\nx\nb\nc"),
    ],
  }).state;
  // 相对打开基线：第 2 行（原 x → b）与其后内容对齐差异 → 直接构造已保存修改
  // saved="a\nb\nc"，opened="a\nx\nb\nc"：先制造绿标
  state = state.update({
    changes: { from: 0, to: state.doc.length, insert: "a\nB\nc" },
    effects: [setSavedTextEffect.of("a\nB\nc")],
  }).state;
  assert.deepEqual([...fieldOf(state).saved].sort(), [2]);
  // 删除第 2 行 → 冻结绿集越界行过滤、橙集重算
  state = state.update({ changes: { from: 2, to: 4 } }).state;
  const field = fieldOf(state);
  for (const line of field.saved) {
    assert.ok(line <= state.doc.lines, "绿集行号越界");
  }
});
