import test from "node:test";
import assert from "node:assert/strict";

import {
  createTabViewRegistry,
  default as singletonRegistry,
} from "../src/lib/editor/tabViews.js";

test("登记表存取 EditorState 存档与外观配置", () => {
  const registry = createTabViewRegistry();
  const fakeState = { doc: { toString: () => "A" } };
  assert.equal(registry.getState("a.tex"), undefined);
  assert.equal(registry.getConfig("a.tex"), null);
  registry.setState("a.tex", fakeState, { softWrap: true });
  assert.equal(registry.getState("a.tex"), fakeState);
  assert.deepEqual(registry.getConfig("a.tex"), { softWrap: true });
  // 覆盖登记
  const next = { doc: { toString: () => "A2" } };
  registry.setState("a.tex", next);
  assert.equal(registry.getState("a.tex"), next);
});

test("滚动位置记忆与 drop 清理", () => {
  const registry = createTabViewRegistry();
  registry.setState("a.tex", {});
  registry.setScroll("a.tex", 320);
  assert.equal(registry.getScroll("a.tex"), 320);
  assert.equal(registry.getScroll("missing.tex"), 0);
  registry.drop("a.tex");
  assert.equal(registry.getState("a.tex"), undefined);
  assert.equal(registry.getScroll("a.tex"), 0);
});

test("clear 清空全部登记（项目切换守卫）", () => {
  const registry = createTabViewRegistry();
  registry.setState("a.tex", {}, {});
  registry.setState("b.tex", {}, {});
  registry.setScroll("a.tex", 10);
  registry.clear();
  assert.equal(registry.getState("a.tex"), undefined);
  assert.equal(registry.getState("b.tex"), undefined);
  assert.equal(registry.getScroll("a.tex"), 0);
});

test("支持注入初始映射（测试隔离）与页面级单例导出", () => {
  const states = new Map();
  const scrolls = new Map([["a.tex", 42]]);
  const registry = createTabViewRegistry(states, scrolls);
  assert.equal(registry.getScroll("a.tex"), 42);
  assert.equal(typeof singletonRegistry.getState, "function");
});
