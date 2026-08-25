import test from "node:test";
import assert from "node:assert/strict";

import {
  applyUnifiedDiff,
  parseUnifiedDiff,
} from "../src/lib/editor/applyDiff.js";

const BUFFER = [
  "\\documentclass{article}",
  "\\begin{document}",
  "Hello world.",
  "Second line.",
  "\\end{document}",
].join("\n");

test("parseUnifiedDiff 解析 hunk 头与增删改行", () => {
  const parsed = parseUnifiedDiff(
    ["@@ -3,1 +3,1 @@", "-Hello world.", "+Hola mundo."].join("\n"),
  );
  assert.equal(parsed.ok, true);
  assert.equal(parsed.hunks.length, 1);
  assert.equal(parsed.additions, 1);
  assert.equal(parsed.deletions, 1);
});

test("parseUnifiedDiff 拒绝空文本与无 hunk 文本", () => {
  assert.equal(parseUnifiedDiff("").ok, false);
  assert.equal(parseUnifiedDiff("not a diff").ok, false);
  assert.equal(parseUnifiedDiff(null).ok, false);
});

test("applyUnifiedDiff 替换单行（上下文锚定）", () => {
  const result = applyUnifiedDiff(
    BUFFER,
    [
      "@@ -1,5 +1,5 @@",
      " \\documentclass{article}",
      " \\begin{document}",
      "-Hello world.",
      "+Hola mundo.",
      " Second line.",
      " \\end{document}",
    ].join("\n"),
  );
  assert.equal(result.ok, true);
  assert.ok(result.result.includes("Hola mundo."));
  assert.ok(!result.result.includes("Hello world."));
  // 其余行原样保留
  assert.ok(result.result.includes("\\documentclass{article}"));
  assert.ok(result.result.endsWith("\\end{document}"));
});

test("applyUnifiedDiff 支持纯插入与纯删除", () => {
  const insert = applyUnifiedDiff(
    BUFFER,
    ["@@ -4,0 +5,2 @@", "+Inserted A.", "+Inserted B."].join("\n"),
  );
  assert.equal(insert.ok, true);
  assert.ok(insert.result.includes("Inserted A.\nInserted B."));

  const del = applyUnifiedDiff(
    insert.result,
    ["@@ -6 +6,0 @@", "-Second line."].join("\n"),
  );
  assert.equal(del.ok, true);
  assert.ok(!del.result.includes("Second line."));
});

test("applyUnifiedDiff 多 hunk 且行号漂移时仍整体成功", () => {
  // hunk 行号故意偏移：靠上下文锚定搜索定位
  const result = applyUnifiedDiff(
    BUFFER,
    [
      "@@ -99,3 +99,7 @@",
      " \\begin{document}",
      "-Hello world.",
      "+Hola mundo.",
      " Second line.",
      "@@ -150,2 +154,4 @@",
      " Second line.",
      "+\\section{New}",
      " \\end{document}",
    ].join("\n"),
  );
  assert.equal(result.ok, true);
  assert.ok(result.result.includes("Hola mundo."));
  assert.ok(result.result.includes("\\section{New}"));
  const lines = result.result.split("\n");
  assert.deepEqual(lines, [
    "\\documentclass{article}",
    "\\begin{document}",
    "Hola mundo.",
    "Second line.",
    "\\section{New}",
    "\\end{document}",
  ]);
});

test("applyUnifiedDiff 容忍 hunk 之间的空行分隔（LLM 常见输出）", () => {
  // 回归（阶段四评审）：hunk 间空行曾被误当作空 context 行追加到前一个
  // hunk 的旧块，导致锚定失配、整份有效 diff 被误判 hunk_not_matched
  const result = applyUnifiedDiff(
    BUFFER,
    [
      "@@ -1,2 +1,2 @@",
      " \\documentclass{article}",
      "-\\begin{document}",
      "+\\begin{document} % ok",
      "",
      "@@ -5 +5 @@",
      " \\end{document}",
    ].join("\n"),
  );
  assert.equal(result.ok, true, `expected ok, got ${JSON.stringify(result)}`);
  assert.ok(result.result.includes("\\begin{document} % ok"));

  // 连续多个空行分隔同样跳过
  const multi = applyUnifiedDiff(
    BUFFER,
    [
      "@@ -3 +3 @@",
      "-Hello world.",
      "+Hola mundo.",
      "",
      "",
      "@@ -4 +4 @@",
      " Second line.",
    ].join("\n"),
  );
  assert.equal(multi.ok, true);
  assert.ok(multi.result.includes("Hola mundo."));
});

test("parseUnifiedDiff 保留 hunk 内部的空 context 行", () => {
  // 分隔空行跳过不影响真正的空 context 行（缓冲区中的空行）：
  // 裸空行后继不是 hunk 头时仍按空 context 处理
  const parsed = parseUnifiedDiff(
    ["@@ -1,3 +1,3 @@", " a", "", " c", "-x", "+y"].join("\n"),
  );
  assert.equal(parsed.ok, true);
  assert.deepEqual(
    parsed.hunks[0].lines.map((l) => `${l.type}:${l.text}`),
    ["context:a", "context:", "context:c", "del:x", "add:y"],
  );
});

test("applyUnifiedDiff 任一 hunk 无法匹配则整体失败", () => {
  const result = applyUnifiedDiff(
    BUFFER,
    [
      "@@ -1,2 +1,2 @@",
      " \\documentclass{article}",
      "-NO SUCH LINE",
      "+x",
    ].join("\n"),
  );
  assert.equal(result.ok, false);
  assert.equal(result.reason, "hunk_not_matched");
});

test("applyUnifiedDiff 对空/畸形 diff 报告原因", () => {
  assert.deepEqual(applyUnifiedDiff(BUFFER, ""), {
    ok: false,
    reason: "empty_diff",
  });
  const bad = applyUnifiedDiff(BUFFER, "just some text");
  assert.equal(bad.reason, "malformed_diff");
});

test("applyUnifiedDiff 忽略 \\ No newline 标记与文件头", () => {
  const result = applyUnifiedDiff(
    BUFFER,
    [
      "--- a/main.tex",
      "+++ b/main.tex",
      "@@ -3 +3 @@",
      "-Hello world.",
      "+Hola mundo.",
      "\\ No newline at end of file",
    ].join("\n"),
  );
  assert.equal(result.ok, true);
  assert.ok(result.result.includes("Hola mundo."));
});
