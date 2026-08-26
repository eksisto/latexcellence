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
  // hunk 行号故意偏移：靠上下文锚定搜索定位。
  // 注意两 hunk 的源区间不得共享缓冲区行（hunk1 截去尾随 context 行），
  // 否则按重叠校验整体判 hunk_overlap
  const result = applyUnifiedDiff(
    BUFFER,
    [
      "@@ -99,3 +99,7 @@",
      " \\begin{document}",
      "-Hello world.",
      "+Hola mundo.",
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

test("applyUnifiedDiff 旧块在窗口内多处命中报 hunk_ambiguous", () => {
  // 同一旧块 \end{itemize} 在 ±MAX_SEARCH_OFFSET 窗口内出现两次：
  // 应用位置不确定，宁整体失败也不误改
  const buffer = [
    "\\begin{itemize}",
    "\\item 阿尔法",
    "\\end{itemize}",
    "正文一行。",
    "\\begin{itemize}",
    "\\item 贝塔",
    "\\end{itemize}",
  ].join("\n");
  const result = applyUnifiedDiff(
    buffer,
    ["@@ -3 +3 @@", "-\\end{itemize}", "+\\end{itemize}[修改]"].join("\n"),
  );
  assert.deepEqual(result, { ok: false, reason: "hunk_ambiguous" });
});

test("applyUnifiedDiff 行号漂移但唯一命中仍正常应用", () => {
  // 防回归：歧义检测不得误伤"行号严重失真但旧块全文唯一"的正常锚定
  const result = applyUnifiedDiff(
    BUFFER,
    ["@@ -99,1 +99,1 @@", "-Second line.", "+第二行（已改）。"].join("\n"),
  );
  assert.equal(result.ok, true);
  assert.deepEqual(result.result.split("\n"), [
    "\\documentclass{article}",
    "\\begin{document}",
    "Hello world.",
    "第二行（已改）。",
    "\\end{document}",
  ]);
});

test("applyUnifiedDiff 两个替换 hunk 源区间重叠报 hunk_overlap", () => {
  // hunk1 源区间 [Hello world., Second line.] 与 hunk2
  // [Second line., \end{document}] 在 Second line. 处相交
  const result = applyUnifiedDiff(
    BUFFER,
    [
      "@@ -3,2 +3,2 @@",
      " Hello world.",
      "-Second line.",
      "+SECOND。",
      "@@ -4,2 +4,2 @@",
      " Second line.",
      "-\\end{document}",
      "+END。",
    ].join("\n"),
  );
  assert.deepEqual(result, { ok: false, reason: "hunk_overlap" });
});

test("applyUnifiedDiff 插入点落入替换区间内部报 hunk_overlap", () => {
  // 替换 hunk 覆盖缓冲区下标 [3,5)，纯插入 hunk 指向下标 4（区间内部）
  const result = applyUnifiedDiff(
    BUFFER,
    [
      "@@ -4,0 +5,1 @@" /* 先声明的插入 hunk，检验升序排序后仍判重叠 */,
      "+插入注。",
      "@@ -4,2 +4,2 @@",
      " Second line.",
      "-\\end{document}",
      "+\\end{document} % 改",
    ].join("\n"),
  );
  assert.deepEqual(result, { ok: false, reason: "hunk_overlap" });
});

test("applyUnifiedDiff 插入点恰在替换区间边界之后正常应用", () => {
  // 替换源区间 [3,4)，插入点 4 恰在其后：不相交，照常应用
  const result = applyUnifiedDiff(
    BUFFER,
    [
      "@@ -4 +4 @@",
      "-Second line.",
      "+第二行改。",
      "@@ -4,0 +5,1 @@",
      "+新增行。",
    ].join("\n"),
  );
  assert.equal(result.ok, true);
  assert.deepEqual(result.result.split("\n"), [
    "\\documentclass{article}",
    "\\begin{document}",
    "Hello world.",
    "第二行改。",
    "新增行。",
    "\\end{document}",
  ]);
});

test("applyUnifiedDiff 两个同位置纯插入 hunk 正常应用", () => {
  // 纯插入源区间长度记 0：同位置两插入不相交，必须放行
  const result = applyUnifiedDiff(
    BUFFER,
    ["@@ -3,0 +4,1 @@", "+插入甲。", "@@ -3,0 +4,1 @@", "+插入乙。"].join("\n"),
  );
  assert.equal(result.ok, true);
  // 正向组装按锚点升序、稳定排序：同位置插入按 diff 声明次序落位（甲在乙之前）
  assert.deepEqual(result.result.split("\n"), [
    "\\documentclass{article}",
    "\\begin{document}",
    "Hello world.",
    "插入甲。",
    "插入乙。",
    "Second line.",
    "\\end{document}",
  ]);
});

test("applyUnifiedDiff EOF 无尾换行场景不受歧义/重叠校验破坏", () => {
  const eofBuffer = [
    "\\documentclass{article}",
    "\\begin{document}",
    "末行内容。",
  ].join("\n"); // 无尾随换行
  const ok = applyUnifiedDiff(
    eofBuffer,
    ["@@ -99 +99 @@", "-末行内容。", "+末行已修订。"].join("\n"),
  );
  assert.equal(ok.ok, true);
  assert.equal(
    ok.result,
    "\\documentclass{article}\n\\begin{document}\n末行已修订。",
  );

  // EOF 附近的多处命中依旧报歧义（\end{quote} 出现两次，均无尾随换行）
  const dupBuffer = [
    "\\begin{quote}",
    "引文。",
    "\\end{quote}",
    "",
    "\\begin{quote}",
    "又一段。",
    "\\end{quote}",
  ].join("\n");
  const ambiguous = applyUnifiedDiff(
    dupBuffer,
    ["@@ -7 +7 @@", "-\\end{quote}", "+\\end{quote*}"].join("\n"),
  );
  assert.deepEqual(ambiguous, { ok: false, reason: "hunk_ambiguous" });
});

test("applyUnifiedDiff 同锚点「纯插入在前 + 替换在后」按 git 语义相邻应用", () => {
  // @@ -5,0 插到旧第 5 行之后；@@ -6,4 替换旧第 6-9 行 —— 二者锚点
  // 相邻兼容。旧倒序 splice 方案会让插入先行落位、随后同锚点的消费型
  // splice 把刚插入的行当旧内容删掉（静默损坏）
  const buffer = "l1\nl2\nl3\nl4\nl5\na\nb\nc\nl9";
  const result = applyUnifiedDiff(
    buffer,
    [
      "@@ -5,0 +6,2 @@",
      "+NEW1",
      "+NEW2",
      "@@ -6,4 +8,4 @@",
      " a",
      "-b",
      "-c",
      "+B",
      "+C",
      " l9",
    ].join("\n"),
  );
  assert.equal(result.ok, true);
  assert.equal(result.result, "l1\nl2\nl3\nl4\nl5\nNEW1\nNEW2\na\nB\nC\nl9");
});

test("applyUnifiedDiff 同锚点「替换在前 + 纯插入在后」不再误判重叠", () => {
  // 与上一条同一 git 语义、仅 hunk 声明次序相反：正向组装下结果一致
  const buffer = "l1\nl2\nl3\nl4\nl5\na\nb\nc\nl9";
  const result = applyUnifiedDiff(
    buffer,
    [
      "@@ -6,4 +8,4 @@",
      " a",
      "-b",
      "-c",
      "+B",
      "+C",
      " l9",
      "@@ -5,0 +8,2 @@",
      "+NEW1",
      "+NEW2",
    ].join("\n"),
  );
  assert.equal(result.ok, true);
  assert.equal(result.result, "l1\nl2\nl3\nl4\nl5\nNEW1\nNEW2\na\nB\nC\nl9");
});

test("applyUnifiedDiff 纯插入锚点落在消费区间内部仍拒绝", () => {
  const buffer = "l1\nl2\nl3\nl4\nl5";
  const result = applyUnifiedDiff(
    buffer,
    [
      "@@ -3,3 +3,3 @@",
      " l3",
      "-l4",
      "+X",
      " l5",
      "@@ -4,0 +5,1 @@",
      "+Y",
    ].join("\n"),
  );
  // 插入点（旧第 4 行后）落在替换区间 [3,6) 内部：位置不确定，整体失败
  assert.deepEqual(result, { ok: false, reason: "hunk_overlap" });
});

test("applyUnifiedDiff 纯插入紧贴替换块边界仍放行", () => {
  // 锚在消费区间终点（替换 [1,3) 后插入）——合法的相邻形态
  const result = applyUnifiedDiff(
    BUFFER,
    [
      "@@ -3,2 +3,3 @@",
      "-Hello world.",
      "+Hola.",
      "+Mundo.",
      "@@ -5,0 +6,1 @@",
      "+附注。",
    ].join("\n"),
  );
  assert.equal(result.ok, true);
  assert.equal(
    result.result,
    "\\documentclass{article}\n\\begin{document}\nHola.\nMundo.\nSecond line.\n\\end{document}\n附注。",
  );
});
