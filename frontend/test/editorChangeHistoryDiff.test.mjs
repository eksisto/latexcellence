import test from "node:test";
import assert from "node:assert/strict";

import {
  computeChangedLines,
  myersChangedBIndices,
} from "../src/lib/editor/changeHistoryDiff.js";

/** Set<number> → 升序数组，便于断言 */
const lines = (set) => [...set].sort((a, b) => a - b);

test("全文相同 → 空集合（键入回显 / 撤回到保存点的高频直通）", () => {
  const text = "\\section{A}\nhello\nworld";
  assert.equal(computeChangedLines(text, text).size, 0);
});

test("单行编辑只标该行", () => {
  const changed = computeChangedLines("a\nb\nc", "a\nB\nc");
  assert.deepEqual(lines(changed), [2]);
});

test("行尾追加新行：只标新增行", () => {
  const changed = computeChangedLines("a\nb", "a\nb\nnew1\nnew2");
  assert.deepEqual(lines(changed), [3, 4]);
});

test("中部插入：插入行标记，后续行不漂移", () => {
  const changed = computeChangedLines("a\nb\nc", "a\nX\nY\nb\nc");
  assert.deepEqual(lines(changed), [2, 3]);
});

test("纯删除块：after 无对应行，不产生标记（对齐 Notepad++ 行为）", () => {
  const changed = computeChangedLines("a\nb\nc\nd", "a\nd");
  assert.deepEqual(lines(changed), []);
});

test("替换块（删 2 行插 1 行）：只标 after 侧新行", () => {
  const changed = computeChangedLines("a\nb1\nb2\nc", "a\nX\nc");
  assert.deepEqual(lines(changed), [2]);
});

test("多处独立修改：全部命中", () => {
  const before = ["l1", "l2", "l3", "l4", "l5", "l6"].join("\n");
  const after = ["L1", "l2", "l3", "L4", "l5", "l6"].join("\n");
  const changed = computeChangedLines(before, after);
  assert.deepEqual(lines(changed), [1, 4]);
});

test("空文本 → 非空：全部行标记", () => {
  const changed = computeChangedLines("", "one\ntwo");
  assert.deepEqual(lines(changed), [1, 2]);
});

test("非空 → 空文本：剩余的单个空行标记（空文本按 1 行计）", () => {
  const changed = computeChangedLines("one\ntwo", "");
  assert.deepEqual(lines(changed), [1]);
});

test("整文档替换（超中段上限）：护栏生效，全部行标记", () => {
  const n = 7000; // 超过 DIFF_MIDDLE_LINE_CAP(6000)
  const before = Array.from({ length: n }, (_, i) => `old-${i}`).join("\n");
  const after = Array.from({ length: n }, (_, i) => `new-${i}`).join("\n");
  const changed = computeChangedLines(before, after);
  assert.equal(changed.size, n);
  assert.equal(changed.has(1), true);
  assert.equal(changed.has(n), true);
});

test("大面积重排（预算超支护栏）：快速回退整段标记，结果仍然正确", () => {
  const n = 2500;
  const beforeLines = Array.from({ length: n }, (_, i) => `old-${i}`);
  const afterLines = Array.from({ length: n }, (_, i) => `new-${i}`);
  // 全异内容零 snake 步，护栏必须把 frontier 扩张也计入预算：
  // 否则 O(D²) 全量跑满主线程（实测中段 ~190ms）。
  // 直接断言内部函数走护栏路径（确定性，不依赖耗时量级）
  assert.equal(myersChangedBIndices(beforeLines, afterLines), null);
  // 回退路径整段标记，结果仍正确
  const t0 = performance.now();
  const changed = computeChangedLines(
    beforeLines.join("\n"),
    afterLines.join("\n"),
  );
  const elapsed = performance.now() - t0;
  assert.equal(changed.size, n);
  // 护栏生效应在毫秒级完成（回退路径 O(n)）；放宽到 50ms 防慢机误报
  assert.ok(elapsed < 50, `全异中段应走护栏回退，实测 ${elapsed.toFixed(1)}ms`);
});

test("中等规模局部修改仍走精确 Myers（护栏不误伤）", () => {
  const n = 2000;
  const before = Array.from({ length: n }, (_, i) => `line-${i}`);
  const after = before.map((l, i) => (i % 10 === 9 ? `${l}-changed` : l));
  // 200 处单行替换，编辑距离 ~400：frontier 步数在预算内，护栏不得触发
  assert.notEqual(myersChangedBIndices(before.slice(9), after.slice(9)), null);
  const changed = computeChangedLines(before.join("\n"), after.join("\n"));
  // 只标被改的 200 行（1 基的第 10, 20, ..., 2000 行）
  assert.equal(changed.size, 200);
  for (let i = 10; i <= n; i += 10) {
    assert.equal(changed.has(i), true, `line ${i} should be marked`);
  }
  assert.equal(changed.has(1), false);
  assert.equal(changed.has(5), false);
});

test("重排后局部不变：公共前后缀剪除保住未变行", () => {
  const before = "header\n" + "x\n".repeat(50) + "footer";
  const after = "header\n" + "y\n".repeat(50) + "footer";
  const changed = computeChangedLines(before, after);
  assert.equal(changed.size, 50);
  assert.equal(changed.has(1), false);
  assert.equal(changed.has(52), false);
});

test("CRLF 内容按 \\n 切分：\\r 归入行内容参与比较", () => {
  const changed = computeChangedLines("a\r\nb", "a\r\nB");
  assert.deepEqual(lines(changed), [2]);
});
