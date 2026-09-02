import test from "node:test";
import assert from "node:assert/strict";

import { EditorState } from "@codemirror/state";
import { history, isolateHistory, undo, undoDepth } from "@codemirror/commands";

import {
  FORMAT_SHORTCUT_LABEL,
  applyFormatToState,
  buildFormatChanges,
  isFormatShortcut,
  planFormatApply,
  resolveFormatTargetPath,
} from "../src/lib/editor/formatApply.js";
import { replaceTabBufferReducer } from "../src/lib/editor/tabs.js";

/* ── isFormatShortcut：Ctrl/Cmd+Shift+F 判定与键位冲突面 ─────────────── */

test("isFormatShortcut：Ctrl+Shift+F / Cmd+Shift+F 命中（含大写 F）", () => {
  assert.equal(
    isFormatShortcut({ ctrlKey: true, shiftKey: true, key: "f" }),
    true,
  );
  assert.equal(
    isFormatShortcut({ metaKey: true, shiftKey: true, key: "F" }),
    true,
  );
});

test("isFormatShortcut：缺修饰键 / 缺 Shift / 非 f 键均不命中", () => {
  assert.equal(isFormatShortcut({ ctrlKey: true, key: "f" }), false);
  assert.equal(isFormatShortcut({ shiftKey: true, key: "f" }), false);
  assert.equal(
    isFormatShortcut({ ctrlKey: true, shiftKey: true, key: "b" }),
    false,
  );
  // Ctrl+F 是 CM6 内置查找面板，必须不被格式化快捷键吞掉
  assert.equal(
    isFormatShortcut({ ctrlKey: true, shiftKey: false, key: "f" }),
    false,
  );
  assert.equal(isFormatShortcut(null), false);
});

test("FORMAT_SHORTCUT_LABEL 与快捷键常量存在", () => {
  assert.equal(FORMAT_SHORTCUT_LABEL, "Ctrl+Shift+F");
});

/* ── resolveFormatTargetPath：目标 tab 归一化（回归：工具栏点击事件
   对象被当作 filePath 传给 handleFormat 时必须回落激活 tab） ────────── */

test("resolveFormatTargetPath：字符串路径透传", () => {
  assert.equal(resolveFormatTargetPath("main.tex", "other.tex"), "main.tex");
});

test("resolveFormatTargetPath：事件对象 / 空串 / undefined 回落激活 tab", () => {
  const fakeClickEvent = { type: "click" };
  assert.equal(
    resolveFormatTargetPath(fakeClickEvent, "active.tex"),
    "active.tex",
  );
  assert.equal(resolveFormatTargetPath("", "active.tex"), "active.tex");
  assert.equal(resolveFormatTargetPath(undefined, "active.tex"), "active.tex");
  assert.equal(resolveFormatTargetPath(fakeClickEvent, null), null);
});

/* ── buildFormatChanges：整文档替换单事务规格 ────────────────────────── */

test("buildFormatChanges：覆盖整个文档区间", () => {
  assert.deepEqual(buildFormatChanges(11, "formatted"), {
    from: 0,
    to: 11,
    insert: "formatted",
  });
});

/* ── planFormatApply：应用决策 ────────────────────────────────────────── */

const BUFFER = "raw source";
const FORMATTED = "formatted source";

test("planFormatApply：发起 tab 即激活 tab 且视图显示其缓冲区 → view 通路", () => {
  assert.equal(
    planFormatApply({
      targetPath: "main.tex",
      activePath: "main.tex",
      viewDoc: BUFFER,
      archivedDoc: null,
      targetBuffer: BUFFER,
      formatted: FORMATTED,
    }),
    "view",
  );
});

test("planFormatApply：活跃已切走、存档文档与缓冲区一致 → state 通路（历史随存档保留）", () => {
  assert.equal(
    planFormatApply({
      targetPath: "main.tex",
      activePath: "other.tex",
      viewDoc: "other content",
      archivedDoc: BUFFER,
      targetBuffer: BUFFER,
      formatted: FORMATTED,
    }),
    "state",
  );
});

test("planFormatApply：活跃切走且无存档/失配 → store 通路降级", () => {
  assert.equal(
    planFormatApply({
      targetPath: "main.tex",
      activePath: "other.tex",
      viewDoc: "other content",
      archivedDoc: null,
      targetBuffer: BUFFER,
      formatted: FORMATTED,
    }),
    "store",
  );
  assert.equal(
    planFormatApply({
      targetPath: "main.tex",
      activePath: "other.tex",
      viewDoc: "other content",
      archivedDoc: "stale archive",
      targetBuffer: BUFFER,
      formatted: FORMATTED,
    }),
    "store",
  );
});

test("planFormatApply：发起 tab 已被关闭 → skip_missing", () => {
  assert.equal(
    planFormatApply({
      targetPath: "main.tex",
      activePath: "main.tex",
      viewDoc: BUFFER,
      archivedDoc: null,
      targetBuffer: null,
      formatted: FORMATTED,
    }),
    "skip_missing",
  );
});

test("planFormatApply：结果与原文一致 → skip_unchanged（不替换）", () => {
  assert.equal(
    planFormatApply({
      targetPath: "main.tex",
      activePath: "main.tex",
      viewDoc: BUFFER,
      archivedDoc: null,
      targetBuffer: BUFFER,
      formatted: BUFFER,
    }),
    "skip_unchanged",
  );
});

test("planFormatApply：激活但视图文档与缓冲区不一致（换档瞬间）→ 不走 view 通路", () => {
  assert.equal(
    planFormatApply({
      targetPath: "main.tex",
      activePath: "main.tex",
      viewDoc: "transition doc",
      archivedDoc: null,
      targetBuffer: BUFFER,
      formatted: FORMATTED,
    }),
    "store",
  );
});

test("planFormatApply：发起 tab 非激活但激活视图恰好装载相同内容 → 不得走 view 通路（回归：跨 tab 同内容误写）", () => {
  // 发起后台 tab main.tex 格式化后切到 other.tex，other.tex 内容恰好
  // 与 main.tex 缓冲区一致——view 通路会把替换写到 other.tex 的视图，
  // 必须按发起路径锚定改走 state 通路
  assert.equal(
    planFormatApply({
      targetPath: "main.tex",
      activePath: "other.tex",
      viewDoc: BUFFER,
      archivedDoc: BUFFER,
      targetBuffer: BUFFER,
      formatted: FORMATTED,
    }),
    "state",
  );
  // 无存档时降级 store 通路（而非误写激活视图）
  assert.equal(
    planFormatApply({
      targetPath: "main.tex",
      activePath: "other.tex",
      viewDoc: BUFFER,
      archivedDoc: null,
      targetBuffer: BUFFER,
      formatted: FORMATTED,
    }),
    "store",
  );
});

/* ── CM6 history 语义：单事务整文档替换 Ctrl+Z 一步撤销 ──────────────── */

function stateWithDoc(doc) {
  return EditorState.create({ doc, extensions: [history()] });
}

test("applyFormatToState：history 扩展随派生状态保留，undo 一步回原文", () => {
  const state = stateWithDoc(BUFFER);
  const next = applyFormatToState(state, FORMATTED);
  assert.ok(next);
  assert.equal(next.doc.toString(), FORMATTED);
  // 存档 state.update 的事务被 history facet 记录：可撤销深度为 1（一步撤销）
  assert.equal(undoDepth(next), 1);
  // CM6 undo 命令接收 {state, dispatch}（视图同形）：dispatch 收到的
  // 撤销事务自带应用后的新 state（.state），文档应一步回到原文
  let undoTx = null;
  assert.equal(undo({ state: next, dispatch: (tr) => (undoTx = tr) }), true);
  assert.equal(undoTx.state.doc.toString(), BUFFER);
});

test("applyFormatToState：状态不可用 / 内容一致时返回 null（调用方降级）", () => {
  const state = stateWithDoc(BUFFER);
  assert.equal(applyFormatToState(null, FORMATTED), null);
  assert.equal(applyFormatToState(state, BUFFER), null);
});

test("replaceTabBufferReducer：置脏不改激活位/基线，内容一致时原引用返回", () => {
  const session = {
    tabs: [
      {
        filePath: "a.tex",
        buffer: BUFFER,
        baseMd5: "md5-a",
        dirty: false,
        stale: false,
        lastActive: 1,
      },
      {
        filePath: "b.tex",
        buffer: "b",
        baseMd5: "md5-b",
        dirty: false,
        stale: false,
        lastActive: 2,
      },
    ],
    activeTabPath: "b.tex",
  };
  const next = replaceTabBufferReducer(session, "a.tex", FORMATTED);
  assert.equal(next.activeTabPath, "b.tex");
  const tabA = next.tabs.find((t) => t.filePath === "a.tex");
  assert.equal(tabA.buffer, FORMATTED);
  assert.equal(tabA.dirty, true);
  assert.equal(tabA.baseMd5, "md5-a");
  assert.equal(tabA.stale, false);
  // 内容一致：无变化，返回原引用（不触发订阅）
  assert.equal(replaceTabBufferReducer(next, "a.tex", FORMATTED), next);
  // 目标不存在：原引用返回
  assert.equal(replaceTabBufferReducer(next, "gone.tex", "x"), next);
});
