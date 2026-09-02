import test from "node:test";
import assert from "node:assert/strict";

import {
  activateTabReducer,
  activeBaseMd5,
  activeBuffer,
  activeDirty,
  activeFilePath,
  closeTabReducer,
  countDirtyTabs,
  createTabRecord,
  diffTabsForAutosave,
  findTab,
  getActiveTab,
  getDirtyTabsInOrder,
  getTabBuffer,
  markTabSavedReducer,
  openTabReducer,
  planTabClose,
  planTabSave,
  resetSessionReducer,
  setTabBufferReducer,
  setTabStaleReducer,
} from "../src/lib/editor/tabs.js";

const T0 = 1000;

function sessionWithTwo() {
  let s = { tabs: [], activeTabPath: null };
  s = openTabReducer(s, {
    filePath: "a.tex",
    buffer: "A",
    baseMd5: "m-a",
    now: 1,
  });
  s = { ...s, activeTabPath: null }; // openTabReducer 自动激活，这里先清掉再开第二个
  s = openTabReducer(s, {
    filePath: "b.tex",
    buffer: "B",
    baseMd5: "m-b",
    now: 2,
  });
  return s;
}

test("createTabRecord 初始为干净且不陈旧", () => {
  assert.deepEqual(
    createTabRecord({ filePath: "a.tex", buffer: "x", baseMd5: "m" }),
    {
      filePath: "a.tex",
      buffer: "x",
      baseMd5: "m",
      dirty: false,
      stale: false,
      lastActive: 0,
    },
  );
});

test("openTabReducer 新开 tab 追加在末尾并激活", () => {
  const s = sessionWithTwo();
  assert.deepEqual(
    s.tabs.map((t) => t.filePath),
    ["a.tex", "b.tex"],
  );
  assert.equal(s.activeTabPath, "b.tex");
  assert.equal(activeBuffer(s), "B");
  assert.equal(activeBaseMd5(s), "m-b");
  assert.equal(activeDirty(s), false);
});

test("openTabReducer 对已存在 tab 以磁盘内容覆盖并复位脏/陈旧标记", () => {
  let s = sessionWithTwo();
  s = setTabBufferReducer(s, "B-edit", 3);
  s = setTabStaleReducer(s, "b.tex", true);
  assert.equal(activeDirty(s), true);
  // 冲突放弃重载：重新打开已有 tab
  s = openTabReducer(s, {
    filePath: "b.tex",
    buffer: "B-disk",
    baseMd5: "m-b2",
    now: 4,
  });
  assert.equal(activeBuffer(s), "B-disk");
  assert.equal(activeDirty(s), false);
  const tab = findTab(s, "b.tex");
  assert.equal(tab.stale, false);
  assert.equal(tab.baseMd5, "m-b2");
  assert.deepEqual(
    s.tabs.map((t) => t.filePath),
    ["a.tex", "b.tex"],
  );
});

test("activateTabReducer 切换激活位并刷新 lastActive；不存在时原样返回", () => {
  let s = sessionWithTwo();
  const before = findTab(s, "a.tex").lastActive;
  s = activateTabReducer(s, "a.tex", 10);
  assert.equal(s.activeTabPath, "a.tex");
  assert.equal(findTab(s, "a.tex").lastActive, 10);
  assert.notEqual(findTab(s, "a.tex").lastActive, before);
  const same = activateTabReducer(s, "missing.tex", 11);
  assert.equal(same, s);
});

test("setTabBufferReducer 只写激活 tab 并置脏；无激活 tab 时原样返回", () => {
  let s = sessionWithTwo();
  s = activateTabReducer(s, "a.tex", 3);
  s = setTabBufferReducer(s, "A1", 4);
  assert.equal(getTabBuffer(s, "a.tex"), "A1");
  assert.equal(getTabBuffer(s, "b.tex"), "B");
  assert.equal(activeDirty(s), true);
  assert.equal(findTab(s, "a.tex").lastActive, 4);
  const empty = { tabs: [], activeTabPath: null };
  assert.equal(setTabBufferReducer(empty, "x", 1), empty);
});

test("markTabSavedReducer 更新基线、清除陈旧并按快照重算脏标记", () => {
  let s = sessionWithTwo();
  s = activateTabReducer(s, "a.tex", 3);
  s = setTabBufferReducer(s, "A1", 4);
  s = setTabStaleReducer(s, "a.tex", true);
  // 保存快照 = "A1"，在途无新输入 → dirty 清零
  s = markTabSavedReducer(s, {
    filePath: "a.tex",
    md5: "m-a2",
    savedBuffer: "A1",
  });
  let tab = findTab(s, "a.tex");
  assert.equal(tab.baseMd5, "m-a2");
  assert.equal(tab.dirty, false);
  assert.equal(tab.stale, false);
  // 在途期间有新输入 → 保持 dirty
  s = setTabBufferReducer(s, "A2", 5);
  s = markTabSavedReducer(s, {
    filePath: "a.tex",
    md5: "m-a3",
    savedBuffer: "A1",
  });
  tab = findTab(s, "a.tex");
  assert.equal(tab.dirty, true);
  assert.equal(tab.baseMd5, "m-a3");
});

test("setTabStaleReducer 仅在变化时产生新引用", () => {
  let s = sessionWithTwo();
  const same = setTabStaleReducer(s, "a.tex", false);
  assert.equal(same, s);
  const next = setTabStaleReducer(s, "a.tex", true);
  assert.equal(findTab(next, "a.tex").stale, true);
});

test("closeTabReducer 关闭激活 tab 时邻居接管（右优先，末尾回落左）", () => {
  let s = sessionWithTwo();
  s = activateTabReducer(s, "a.tex", 3);
  // 关闭首个：右侧接管
  s = closeTabReducer(s, "a.tex");
  assert.equal(s.activeTabPath, "b.tex");
  // 重开三个，激活中间，关闭中间 → 右侧接管
  s = openTabReducer(s, {
    filePath: "c.tex",
    buffer: "C",
    baseMd5: "m-c",
    now: 4,
  });
  s = openTabReducer(s, {
    filePath: "d.tex",
    buffer: "D",
    baseMd5: "m-d",
    now: 5,
  });
  s = activateTabReducer(s, "b.tex", 6);
  s = closeTabReducer(s, "b.tex");
  assert.equal(s.activeTabPath, "c.tex");
  // 关闭末尾 → 左侧接管
  s = activateTabReducer(s, "d.tex", 7);
  s = closeTabReducer(s, "d.tex");
  assert.equal(s.activeTabPath, "c.tex");
  // 关闭非激活 tab：激活位不变
  s = activateTabReducer(s, "c.tex", 8);
  s = closeTabReducer(s, "a.tex");
  assert.equal(s.activeTabPath, "c.tex");
  // 清空 → null
  s = closeTabReducer(s, "c.tex");
  assert.equal(s.activeTabPath, null);
  assert.equal(s.tabs.length, 0);
});

test("closeTabReducer 对不存在的 tab 原样返回", () => {
  const s = sessionWithTwo();
  assert.equal(closeTabReducer(s, "missing.tex"), s);
});

test("resetSessionReducer 清空 tabs 与激活位", () => {
  const s = sessionWithTwo();
  assert.deepEqual(resetSessionReducer(), { tabs: [], activeTabPath: null });
});

test("脏 tab 选择器：计数与按 tab 条顺序取脏集合", () => {
  let s = sessionWithTwo();
  s = activateTabReducer(s, "a.tex", 3);
  s = setTabBufferReducer(s, "A1", 4); // a 脏
  assert.equal(countDirtyTabs(s.tabs), 1);
  s = activateTabReducer(s, "b.tex", 5);
  s = setTabBufferReducer(s, "B1", 6); // b 脏
  assert.equal(countDirtyTabs(s.tabs), 2);
  assert.deepEqual(
    getDirtyTabsInOrder(s.tabs).map((t) => t.filePath),
    ["a.tex", "b.tex"],
  );
});

test("activeFilePath / getActiveTab 对空会话安全回落", () => {
  const empty = { tabs: [], activeTabPath: null };
  assert.equal(activeFilePath(empty), null);
  assert.equal(getActiveTab(empty), null);
  assert.equal(activeBuffer(empty), "");
  assert.equal(activeBaseMd5(empty), null);
  assert.equal(activeDirty(empty), false);
});

test("planTabSave 前置决策矩阵：脏/干净/缺失/冲突待决", () => {
  const dirty = { filePath: "a.tex", dirty: true, stale: false };
  const clean = { filePath: "a.tex", dirty: false, stale: false };
  assert.equal(planTabSave(dirty), "proceed");
  // 干净 tab 无需保存；tab 已不存在
  assert.equal(planTabSave(clean), "blocked");
  assert.equal(planTabSave(null), "blocked");
  // 冲突弹窗待决期间拦下新保存（防 409 回路）；他 tab 冲突不牵连
  assert.equal(planTabSave(dirty, { conflictPath: "a.tex" }), "blocked");
  assert.equal(planTabSave(dirty, { conflictPath: "b.tex" }), "proceed");
});

test("planTabSave 覆盖保存绕过冲突待决拦截（弹窗出路回归）", () => {
  const dirty = { filePath: "a.tex", dirty: true, stale: false };
  // 回归：覆盖保存是冲突弹窗自己的出路，曾被冲突守卫一并拦截，
  // 导致「覆盖保存」按钮永久 no-op
  assert.equal(
    planTabSave(dirty, { conflictPath: "a.tex", overwrite: true }),
    "proceed",
  );
  assert.equal(planTabSave(dirty, { overwrite: true }), "proceed");
});

test("planTabSave 陈旧分流：手动确认 / 自动保存静默跳过", () => {
  const stale = { filePath: "a.tex", dirty: true, stale: true };
  assert.equal(planTabSave(stale), "stale_confirm");
  assert.equal(planTabSave(stale, { autosave: true }), "blocked");
  assert.equal(planTabSave(stale, { confirmedStale: true }), "proceed");
  // 覆盖保存显式越过陈旧确认（弹窗出路）
  assert.equal(planTabSave(stale, { overwrite: true }), "proceed");
  // 非陈旧脏 tab 不受确认通道影响
  assert.equal(
    planTabSave(
      { filePath: "a.tex", dirty: true, stale: false },
      { confirmedStale: true },
    ),
    "proceed",
  );
});

test("planTabClose 干净/缺失直接关，脏转三选", () => {
  let s = sessionWithTwo();
  s = activateTabReducer(s, "a.tex", 3);
  assert.equal(planTabClose(s, "missing.tex"), "close");
  assert.equal(planTabClose(s, "b.tex"), "close");
  s = setTabBufferReducer(s, "A1", 4);
  assert.equal(planTabClose(s, "a.tex"), "confirm");
});

test("diffTabsForAutosave 新增 tab 计入武装（刷新恢复补武装回归）", () => {
  const restored = [
    { filePath: "a.tex", buffer: "A", dirty: true, stale: false },
    { filePath: "b.tex", buffer: "B", dirty: false, stale: false },
  ];
  // 回归：刷新恢复的脏 tab 在 hook 挂载对齐之后才出现，旧实现只遍历
  // prev.tabs，恢复后的脏 tab 不触摸就永不自动保存
  const { toArm, toClear } = diffTabsForAutosave([], restored);
  assert.deepEqual([...toArm].sort(), ["a.tex", "b.tex"]);
  assert.deepEqual(toClear, []);
});

test("diffTabsForAutosave 内容/脏标/陈旧变化武装，无变化不动", () => {
  const before = [
    { filePath: "a.tex", buffer: "A", dirty: true, stale: false },
    { filePath: "b.tex", buffer: "B", dirty: false, stale: false },
  ];
  // 键入（buffer 变化）
  assert.deepEqual(
    diffTabsForAutosave(before, [
      { filePath: "a.tex", buffer: "A2", dirty: true, stale: false },
      before[1],
    ]).toArm,
    ["a.tex"],
  );
  // 脏标翻转（保存成功）
  assert.deepEqual(
    diffTabsForAutosave(before, [
      { filePath: "a.tex", buffer: "A", dirty: false, stale: false },
      before[1],
    ]).toArm,
    ["a.tex"],
  );
  // 陈旧标记回写
  assert.deepEqual(
    diffTabsForAutosave(before, [
      before[0],
      { filePath: "b.tex", buffer: "B", dirty: false, stale: true },
    ]).toArm,
    ["b.tex"],
  );
  // 完全无变化 → 双空
  assert.deepEqual(diffTabsForAutosave(before, before), {
    toArm: [],
    toClear: [],
  });
});

test("diffTabsForAutosave 移除 tab 计入清除", () => {
  const tabs = [
    { filePath: "a.tex", buffer: "A", dirty: true, stale: false },
    { filePath: "b.tex", buffer: "B", dirty: false, stale: false },
  ];
  const { toArm, toClear } = diffTabsForAutosave(tabs, [tabs[0]]);
  assert.deepEqual(toArm, []);
  assert.deepEqual(toClear, ["b.tex"]);
});
