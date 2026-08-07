import { test } from "node:test";
import assert from "node:assert/strict";
import {
  buildAnchors,
  collapseWhitespace,
  cumulativeShift,
  locateSpan,
  mapCollapsedIndex,
  nearestOccurrence,
} from "./anchors.js";

test("collapseWhitespace 折叠换行/缩进为单个空格", () => {
  assert.equal(collapseWhitespace("a\n  b\t c"), "a b c");
});

test("mapCollapsedIndex 把折叠位置映射回真实下标", () => {
  const text = "ab\n  cd";
  // 折叠后为 "ab cd"：'c' 在折叠下标 3，对应真实下标 5
  assert.equal(mapCollapsedIndex(text, 3), 5);
  // 折叠结尾（下标 5）对应真实结尾 7
  assert.equal(mapCollapsedIndex(text, 5), 7);
});

test("locateSpan 精确匹配返回真实区间", () => {
  const text = "A \\\\ and B \\\\ end.";
  const span = locateSpan(text, "\\\\", 2);
  assert.deepEqual(span, { start: 2, end: 4 });
});

test("locateSpan 空白不一致时宽松匹配并返回真实区间", () => {
  const text = "line1\n    \\begin{center}\n      text here\n    \\end{center}";
  const needle = "\\begin{center}\ntext here";
  const span = locateSpan(text, needle);
  assert.ok(span);
  // 替换真实区间后，其内容折叠空白应与 needle 折叠一致
  const replaced = text.slice(0, span.start) + text.slice(span.end);
  assert.ok(!collapseWhitespace(replaced).includes("text here"));
  assert.equal(
    collapseWhitespace(text.slice(span.start, span.end)),
    collapseWhitespace(needle),
  );
});

test("locateSpan 取距 target 最近的一处匹配", () => {
  const text = "x \\\\ y \\\\ z";
  const span = locateSpan(text, "\\\\", 8);
  assert.equal(span.start, 7);
  const first = locateSpan(text, "\\\\", 0);
  assert.equal(first.start, 2);
});

test("locateSpan 无匹配返回 null", () => {
  assert.equal(locateSpan("abc", "\\begin"), null);
  assert.equal(locateSpan("abc", ""), null);
});

test("buildAnchors 重复片段按出现顺序消费并升序排列", () => {
  const formatted = "A \\\\ then B \\\\ end.";
  const anchors = buildAnchors(formatted, [
    { original: "\\\\", description: "first" },
    { original: "\\\\", description: "second" },
  ]);
  assert.deepEqual(
    anchors.map((a) => [a.index, a.offset]),
    [
      [1, 2],
      [2, 12],
    ],
  );
});

test("buildAnchors 精确失败时用宽松匹配兜底", () => {
  const formatted = "\\begin{center}\n  text\n\\end{center}";
  const anchors = buildAnchors(formatted, [
    { original: "\\begin{center}\ntext", description: "x" },
  ]);
  assert.equal(anchors.length, 1);
  assert.equal(anchors[0].offset, formatted.indexOf("\\begin{center}"));
});

test("buildAnchors 跳过无法定位的建议", () => {
  const anchors = buildAnchors("abc", [
    { original: "\\begin", description: "missing" },
    { original: "abc", description: "found" },
  ]);
  assert.deepEqual(anchors, [{ index: 2, offset: 0, description: "found" }]);
});

test("nearestOccurrence 平手取更靠前者", () => {
  const text = "a bb b";
  assert.equal(nearestOccurrence(text, "b", 4), 3);
  assert.equal(nearestOccurrence(text, "b", 1), 2);
  assert.equal(nearestOccurrence(text, "z", 0), -1);
});

test("cumulativeShift 累计锚点左侧已采纳编辑的位移", () => {
  const edits = [
    { offset: 2, removedLen: 2, addedLen: 4 },
    { offset: 10, removedLen: 1, addedLen: 1 },
  ];
  assert.equal(cumulativeShift(edits, 2), 2);
  assert.equal(cumulativeShift(edits, 8), 2);
  assert.equal(cumulativeShift(edits, 10), 2);
  assert.equal(cumulativeShift([], 0), 0);
});

function adoptAll(formatted, suggestions, order) {
  let text = formatted;
  const edits = [];
  for (const idx of order) {
    const s = suggestions[idx];
    const offset = buildAnchors(formatted, suggestions).find(
      (a) => a.index === idx + 1,
    )?.offset;
    const estimated =
      offset === undefined ? 0 : offset + cumulativeShift(edits, offset);
    const span = locateSpan(text, s.original, estimated);
    assert.ok(span, `suggestion ${idx + 1} 应能找到匹配`);
    text = text.slice(0, span.start) + s.suggested + text.slice(span.end);
    edits.push({
      offset,
      removedLen: span.end - span.start,
      addedLen: s.suggested.length,
    });
  }
  return text;
}

test("重复片段按锚点顺序与逆序采纳都落在各自位置", () => {
  const formatted = "A \\\\ and B \\\\ end.";
  const suggestions = [
    { original: "\\\\", suggested: "\\par" },
    { original: "\\\\", suggested: "\\par" },
  ];
  const forward = adoptAll(formatted, suggestions, [0, 1]);
  assert.equal(forward, "A \\par and B \\par end.");
  const backward = adoptAll(formatted, suggestions, [1, 0]);
  assert.equal(backward, "A \\par and B \\par end.");
});

test("空白不一致的建议片段也能被采纳替换", () => {
  const formatted = "\\begin{center}\n  text\n\\end{center}";
  const suggestions = [
    { original: "\\begin{center}\ntext", suggested: "\\begin{center}text" },
  ];
  const result = adoptAll(formatted, suggestions, [0]);
  assert.equal(result, "\\begin{center}text\n\\end{center}");
});
