import test from "node:test";
import assert from "node:assert/strict";

import { countDocStats, createStatsHub } from "../src/lib/editor/stats.js";

test("countDocStats 空文本为 1 行 0 字（对齐 CM6 空 doc 口径）", () => {
  assert.deepEqual(countDocStats(""), {
    lines: 1,
    cjkChars: 0,
    latinWords: 0,
    totalWords: 0,
  });
});

test("countDocStats 行数按 \\n 计（含尾随换行）", () => {
  assert.equal(countDocStats("a\nb").lines, 2);
  assert.equal(countDocStats("a\n").lines, 2);
  assert.equal(
    countDocStats("\\section{A}\n\\begin{itemize}\n\\end{itemize}").lines,
    3,
  );
});

test("countDocStats 西文词：连续段计数，连字符不拆分", () => {
  assert.equal(countDocStats("hello world").latinWords, 2);
  assert.equal(countDocStats("well-known factorization").latinWords, 2);
  assert.equal(countDocStats("v2 2024 x").latinWords, 3);
  // 撇号连字
  assert.equal(countDocStats("it’s fine").latinWords, 2);
});

test("countDocStats CJK 逐字计数，不含全角标点", () => {
  assert.equal(countDocStats("中文标点。").cjkChars, 4);
  assert.equal(countDocStats("第二行两个词").cjkChars, 6);
});

test("countDocStats 中英混排合计", () => {
  const stats = countDocStats("hello world\n第二行 with 两个词");
  assert.equal(stats.lines, 2);
  assert.equal(stats.latinWords, 3); // hello / world / with
  assert.equal(stats.cjkChars, 6); // 第二行(3) + 两个词(3)
  assert.equal(stats.totalWords, stats.cjkChars + stats.latinWords);
});

test("countDocStats LaTeX 命令按原文近似统计（不剥语法）", () => {
  const stats = countDocStats("\\textbf{bold} text");
  assert.equal(stats.latinWords, 3); // textbf / bold / text
});

test("createStatsHub：发布 / 订阅 / 快照引用稳定", () => {
  const hub = createStatsHub();
  assert.equal(hub.getSnapshot(), null);

  const seen = [];
  const unsub = hub.subscribe(() => seen.push(hub.getSnapshot()));

  const first = { line: 1, col: 1, selectedChars: 0, lines: 10, words: 20 };
  hub.publish(first);
  assert.equal(hub.getSnapshot(), first);
  assert.equal(seen.length, 1);

  // 同值发布：引用不变、不通知（useSyncExternalStore 免无效刷新）
  hub.publish({ line: 1, col: 1, selectedChars: 0, lines: 10, words: 20 });
  assert.equal(hub.getSnapshot(), first);
  assert.equal(seen.length, 1);

  const second = { line: 2, col: 3, selectedChars: 5, lines: 10, words: 20 };
  hub.publish(second);
  assert.equal(hub.getSnapshot(), second);
  assert.equal(seen.length, 2);

  unsub();
  hub.publish({ line: 9, col: 9, selectedChars: 0, lines: 1, words: 0 });
  assert.equal(seen.length, 2);
});

test("createStatsHub：监听器无参调用，快照消费方须经 getSnapshot() 回读", () => {
  // 契约钉子（editor v3 阶段一回归）：subscribe 的 listener 以零参数调用
  // （JSDoc `() => void`），回调形参不会拿到快照。EditorPage 正向同步的
  // 光标快照捕获（防抖前回读 getSnapshot 进 ref）依赖本契约——若 hub
  // 未来改为向监听器传参，此处先行报错以同步审视两端消费方。
  const hub = createStatsHub();
  const receivedArgs = [];
  const captured = [];
  const unsub = hub.subscribe((...args) => {
    receivedArgs.push(args.length);
    // 正确消费形态：回调内回读 getSnapshot（EditorPage / EditorStatusBar 同款）
    captured.push(hub.getSnapshot());
  });

  const stats = { line: 7, col: 4, selectedChars: 0, lines: 30, words: 80 };
  hub.publish(stats);

  assert.deepEqual(receivedArgs, [0], "监听器必须以零参数调用");
  assert.equal(captured.length, 1);
  assert.equal(captured[0], stats, "getSnapshot() 须返回刚发布的快照");

  unsub();
});
