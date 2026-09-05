import test from "node:test";
import assert from "node:assert/strict";

import {
  computeDiagnostics,
  DIAGNOSTICS_LIMIT,
  DIAG_RULE_UNMATCHED_ENV,
  DIAG_RULE_UNBALANCED_BRACES,
  DIAG_NOTE_TRUNCATED,
} from "../src/lib/editor/diagnostics.js";

const ENV = DIAG_RULE_UNMATCHED_ENV;
const BRACE = DIAG_RULE_UNBALANCED_BRACES;

/** 提取简断言视图：[rule, kind, env?, line?] */
const brief = (items) =>
  items.map((d) => [
    d.rule,
    d.params.kind,
    d.params.env ?? null,
    d.params.line,
  ]);

test("computeDiagnostics：配平文档不产生诊断", () => {
  const doc = [
    "\\documentclass{article}",
    "\\begin{document}",
    "\\section{引言}",
    "正文 $a^{b}_{c}$ 与 \\textbf{粗体}。",
    "\\begin{itemize}\\item{x}\\end{itemize}",
    "\\end{document}",
  ].join("\n");
  assert.deepEqual(computeDiagnostics(doc), []);
});

test("computeDiagnostics：非字符串与空输入返回空数组", () => {
  assert.deepEqual(computeDiagnostics(null), []);
  assert.deepEqual(computeDiagnostics(undefined), []);
  assert.deepEqual(computeDiagnostics(42), []);
  assert.deepEqual(computeDiagnostics(""), []);
});

test("unmatched-env：未闭合环境报 error，定位 \\begin token 区间与所在行", () => {
  const doc = [
    "\\begin{document}",
    "text",
    "\\begin{itemize}",
    "never ends",
    "\\end{document}",
  ].join("\n");
  const items = computeDiagnostics(doc);
  assert.equal(items.length, 1);
  const d = items[0];
  assert.equal(d.rule, ENV);
  assert.equal(d.severity, "error");
  assert.equal(d.params.kind, "unclosed");
  assert.equal(d.params.env, "itemize");
  assert.equal(d.params.line, 3);
  const beginIdx = doc.indexOf("\\begin{itemize}");
  assert.equal(d.from, beginIdx);
  assert.equal(d.to, beginIdx + "\\begin{itemize}".length);
  assert.ok(d.message.includes("itemize"));
});

test("unmatched-env：多余 \\end 报 stray，配平 \\end 不报", () => {
  const doc = "\\begin{center}x\\end{center}\\end{center}";
  const items = computeDiagnostics(doc);
  assert.equal(items.length, 1);
  assert.equal(items[0].params.kind, "stray");
  assert.equal(items[0].params.env, "center");
  assert.equal(items[0].params.line, 1);
  const endIdx = doc.lastIndexOf("\\end{center}");
  assert.equal(items[0].from, endIdx);
});

test("unmatched-env：嵌套配平不报；嵌套错乱（外层先闭合）按内层未闭合 + 整段弹出", () => {
  // 正确嵌套
  assert.deepEqual(
    computeDiagnostics("\\begin{a}\\begin{b}x\\end{b}\\end{a}"),
    [],
  );
  // \begin{a}\begin{b}\end{a}：b 未闭合（a 的闭合成立）
  const items = computeDiagnostics("\\begin{a}\n\\begin{b}\n\\end{a}");
  assert.deepEqual(brief(items), [[ENV, "unclosed", "b", 2]]);
  // 交叉换序：\end{itemize} 触发 enumerate 未闭合，\end{enumerate} 成为 stray
  const swapped = computeDiagnostics(
    "\\begin{itemize}\\begin{enumerate}\\end{itemize}\\end{enumerate}",
  );
  assert.deepEqual(brief(swapped), [
    [ENV, "unclosed", "enumerate", 1],
    [ENV, "stray", "enumerate", 1],
  ]);
});

test("unmatched-env：环境名允许 \\begin {env} 空白；带星环境与同名嵌套配平", () => {
  assert.deepEqual(computeDiagnostics("\\begin {align*}x\\end {align*}"), []);
  assert.deepEqual(
    computeDiagnostics(
      "\\begin{itemize}\\begin{itemize}\\end{itemize}\\end{itemize}",
    ),
    [],
  );
});

test("unmatched-env：环境名本行未闭合（畸形/键入中途）按前缀继续栈匹配，不闪报", () => {
  // \begin{itemize（缺右括号）+ 后续 \end{itemize}：前缀名匹配弹出，零误报
  assert.deepEqual(computeDiagnostics("\\begin{itemize\n\\end{itemize}"), []);
  // 键入中途快照：\begin{itemize 未闭合环境在文末报告
  const typing = computeDiagnostics("\\begin{document}\n\\begin{itemize");
  assert.deepEqual(brief(typing), [
    [ENV, "unclosed", "document", 1],
    [ENV, "unclosed", "itemize", 2],
  ]);
  // 仅 \begin{（空环境名前缀）与无匹配的 \end{：静默跳过
  assert.deepEqual(computeDiagnostics("\\begin{\n\\end{"), []);
  // \end{itemiz 键入中途：名字与栈内精确匹配才弹出；未命中不报 stray
  assert.deepEqual(
    brief(computeDiagnostics("\\begin{itemize}\n\\end{itemiz")),
    [[ENV, "unclosed", "itemize", 1]],
  );
  assert.deepEqual(computeDiagnostics("\\end{itemiz"), []);
});

test("unmatched-env：verbatim 类环境整体豁免（清单与 outlineScan 同源）", () => {
  const doc = [
    "\\begin{verbatim}",
    " % 注释符号与 } { 都是字面 \\begin{itemize}",
    "\\end{verbatim}",
    "\\begin{lstlisting}[language=Python]",
    'def f(): return "}}}{{{"',
    "\\end{lstlisting}",
    "\\begin{minted}{python}",
    "x = '}{'",
    "\\end{minted}",
    "\\begin{Verbatim}",
    "raw }}}",
    "\\end{Verbatim}",
  ].join("\n");
  assert.deepEqual(computeDiagnostics(doc), []);
});

test("unmatched-env：verbatim 内注释符号为字面，未闭合 verbatim 文末报告且其后内容字面化", () => {
  // verbatim 未闭合：环境本身报 unclosed；其后 % 与 } 均为字面，不产生花括号诊断
  const items = computeDiagnostics(
    ["\\begin{verbatim}", "raw % 真注释形态也不算 } {", "more }"].join("\n"),
  );
  assert.deepEqual(brief(items), [[ENV, "unclosed", "verbatim", 1]]);
});

test("unbalanced-braces：文末未闭合 { 报在最内层 opener，depth 为未闭合总数", () => {
  const doc = "line1 {\nline2 {x\nend";
  const items = computeDiagnostics(doc);
  assert.equal(items.length, 1);
  const d = items[0];
  assert.equal(d.rule, BRACE);
  assert.equal(d.severity, "warning");
  assert.equal(d.params.kind, "unclosed");
  assert.equal(d.params.line, 2);
  assert.equal(d.params.depth, 2);
  assert.equal(d.from, doc.indexOf("{", doc.indexOf("line2")));
  assert.equal(d.to, d.from + 1);
});

test("unbalanced-braces：多余 } 定位所在行；同多行各自报告", () => {
  const doc = "a\nb}\nc}";
  const items = computeDiagnostics(doc);
  assert.deepEqual(brief(items), [
    [BRACE, "extra-close", null, 2],
    [BRACE, "extra-close", null, 3],
  ]);
  // 每行最多一条：同一行两个多余 } 只报首个
  const merged = computeDiagnostics("}}");
  assert.equal(merged.length, 1);
  assert.equal(merged[0].from, 0);
});

test("注释豁免：整行注释与行内注释不诊断，\\% 转义后注释判定与 outlineScan 同源", () => {
  const doc = [
    "% \\begin{itemize} } {",
    "\\textbf{ok} % 行内注释 \\begin{x}",
    "50\\% }", // \% 为字面百分号，其后 } 是真多余
    "\\\\% 换行命令后真注释 }",
  ].join("\n");
  const items = computeDiagnostics(doc);
  assert.deepEqual(brief(items), [[BRACE, "extra-close", null, 3]]);
});

test("\\verb 豁免：参数内花括号与环境名均为字面；未闭合 verb 吞掉本行余下内容", () => {
  assert.deepEqual(computeDiagnostics("\\verb|\\begin{itemize}|x"), []);
  assert.deepEqual(computeDiagnostics("\\verb*+}{{}+y"), []);
  // 未闭合（行内无第二个定界符）：本行余下内容排除，后续行正常
  const unclosed = computeDiagnostics("\\verb|abc\ngood}");
  assert.deepEqual(brief(unclosed), [[BRACE, "extra-close", null, 2]]);
  // 非法形态（定界符为空白）：不豁免，按普通文本扫描
  const invalid = computeDiagnostics("\\verb x}");
  assert.deepEqual(brief(invalid), [[BRACE, "extra-close", null, 1]]);
});

test("转义花括号：\\{ / \\} 不参与配平", () => {
  assert.deepEqual(computeDiagnostics("\\{\\{a\\}\\}"), []);
  assert.deepEqual(computeDiagnostics("x = \\{y\\} + 1"), []);
});

test("URL 字面量豁免：\\url / \\path / \\href 第一个参数（latex_literals 同源口径）", () => {
  // %20 为字面百分号（不再起注释），参数内花括号配平
  assert.deepEqual(
    computeDiagnostics("\\url{https://a.com/%20foo{x}} 后文{正常}"),
    [],
  );
  // href 第二个参数（显示文本）照常计数
  assert.deepEqual(computeDiagnostics("\\href{u%v}{text}"), []);
  assert.deepEqual(computeDiagnostics("\\path{a%b}"), []);
  // 参数未闭合：余下内容整体按 URL 字面豁免
  assert.deepEqual(computeDiagnostics("\\url{abc"), []);
  // \urlstyle 等更长命令名不误命中
  const notUrl = computeDiagnostics("\\urlstyle{tt}");
  assert.deepEqual(notUrl, []);
});

test("命令名全读：\\endnote 不误判 \\end，命令后的花括号参数照常计数", () => {
  assert.deepEqual(computeDiagnostics("\\endnote{ok}"), []);
  assert.deepEqual(brief(computeDiagnostics("\\end{x}")), [
    [ENV, "stray", "x", 1],
  ]);
  assert.deepEqual(computeDiagnostics("\\textbf{配平}"), []);
  // 未配平的命令参数正常报告
  assert.equal(computeDiagnostics("\\textbf{失衡").length, 1);
});

test("诊断上限：超过 200 条截断，首条标注 truncated/total 并附截断提示", () => {
  const doc = Array.from({ length: 250 }, () => "}").join("\n");
  const items = computeDiagnostics(doc);
  assert.equal(items.length, DIAGNOSTICS_LIMIT);
  assert.equal(items[0].truncated, true);
  assert.equal(items[0].total, 250);
  assert.ok(items[0].message.includes("250"));
  assert.ok(items[0].message.includes(String(DIAGNOSTICS_LIMIT)));
  // 其余条目无截断标注
  assert.equal(items[1].truncated, undefined);
  // 不超限时不带标注
  assert.equal(computeDiagnostics("}")[0].truncated, undefined);
});

test("输出按文档位置升序（文末 unclosed 报告回溯到 \\begin 位置）", () => {
  const doc = "\\begin{a}\n\\begin{b}\nx\n\\end{b}";
  const items = computeDiagnostics(doc);
  assert.deepEqual(brief(items), [[ENV, "unclosed", "a", 1]]);
  // 多诊断按 from 排序
  const multi = computeDiagnostics("\\end{z}\n\\begin{y}");
  assert.deepEqual(brief(multi), [
    [ENV, "stray", "z", 1],
    [ENV, "unclosed", "y", 2],
  ]);
  assert.ok(multi[0].from < multi[1].from);
});

test("formatMessage 注入：按 rule + params 组装三语消息；截断提示同通道", () => {
  const formats = {
    [ENV]: (p) =>
      p.kind === "stray"
        ? `杂散 ${p.env}@${p.line}`
        : `未闭合 ${p.env}@${p.line}`,
    [BRACE]: (p) => `花括号 ${p.kind}@${p.line}`,
    [DIAG_NOTE_TRUNCATED]: (p) => `截断 ${p.total}`,
  };
  const format = (rule, params) => formats[rule](params);
  const items = computeDiagnostics("\\begin{table}x", format);
  assert.deepEqual(
    items.map((d) => d.message),
    ["未闭合 table@1"],
  );

  const truncated = computeDiagnostics(
    Array.from({ length: DIAGNOSTICS_LIMIT + 1 }, () => "}").join("\n"),
    format,
  );
  assert.equal(truncated.length, DIAGNOSTICS_LIMIT);
  assert.ok(truncated[0].message.includes(`花括号 extra-close@1`));
  assert.ok(truncated[0].message.endsWith(`截断 ${DIAGNOSTICS_LIMIT + 1}`));
});

test("数学区域与常规文档综合样例", () => {
  const doc = [
    "\\documentclass{article}",
    "\\begin{document}",
    "行内 $f({x})$ 与 display：",
    "\\begin{equation}",
    "  \\int_{0}^{1} x^{n} \\, dx = \\frac{1}{n+1}",
    "\\end{equation}",
    "\\url{https://t.tex/q%20\\{x\\}}",
    "\\end{document}",
  ].join("\n");
  assert.deepEqual(computeDiagnostics(doc), []);
});

/* ── 栈索引簿记回归（review-fix：防名字计数失配） ────────────────────────── */

test("同名嵌套弹出后计数正确：多余 \\end 仍判 stray，不误命中残留计数", () => {
  // 双层同名配平后，第三个 \end{a} 是 stray
  const items = computeDiagnostics(
    "\\begin{a}\\begin{a}\\end{a}\\end{a}\\end{a}",
  );
  assert.deepEqual(brief(items), [[ENV, "stray", "a", 1]]);
  // verbatim 配对弹出同样维护计数：第二个 \end{verbatim} 是 stray
  const verb = computeDiagnostics(
    "\\begin{verbatim}x\\end{verbatim}\\end{verbatim}",
  );
  assert.deepEqual(brief(verb), [[ENV, "stray", "verbatim", 1]]);
  // 深层命中整段弹出（内层报未闭合）后计数清零：后续同名 \end 均 stray
  const popped = computeDiagnostics(
    "\\begin{a}\\begin{b}\\end{a}\\end{b}\\end{b}",
  );
  assert.deepEqual(brief(popped), [
    [ENV, "unclosed", "b", 1],
    [ENV, "stray", "b", 1],
    [ENV, "stray", "b", 1],
  ]);
  // 命中栈中同名（非栈顶）弹出后，剩余外层照常文末报告
  const mid = computeDiagnostics("\\begin{a}\\begin{b}\\begin{a}\\end{a}");
  assert.deepEqual(brief(mid), [
    [ENV, "unclosed", "a", 1],
    [ENV, "unclosed", "b", 1],
  ]);
});

test("病态输入性能护栏：深环境栈 × 海量 stray \\end 不退化（曾经的平方级卡死）", async () => {
  // 5 万层未闭合 + 5 万个不匹配 \end：修复前逐 \end 全栈扫描约需数秒
  const doc = "\\begin{aaa}\n".repeat(50000) + "\\end{zzz}\n".repeat(50000);
  const t0 = performance.now();
  const items = computeDiagnostics(doc);
  const elapsed = performance.now() - t0;
  assert.ok(elapsed < 2000, `computeDiagnostics 耗时 ${elapsed}ms，疑似退化`);
  // 截断与排序仍正确：文末 unclosed（文档最前）居首，total 精确
  assert.equal(items.length, DIAGNOSTICS_LIMIT);
  assert.equal(items[0].params.kind, "unclosed");
  assert.equal(items[0].params.env, "aaa");
  assert.equal(items[0].total, 100000);
});

test("超限排水：total 保持精确，排水后再开环境/文末收尾照样计入", () => {
  // 300 行多余 } → 触发排水后 total 仍为 300
  const braces = Array.from({ length: 300 }, () => "}").join("\n");
  const items = computeDiagnostics(braces);
  assert.equal(items.length, DIAGNOSTICS_LIMIT);
  assert.equal(items[0].total, 300);
  // 排水后再 \begin{x}：unclosed 条目位置靠后不入前 200，但 total=301
  const mixed =
    Array.from({ length: 300 }, () => "}\n").join("") + "\\begin{x}";
  const mixedItems = computeDiagnostics(mixed);
  assert.equal(mixedItems.length, DIAGNOSTICS_LIMIT);
  assert.equal(mixedItems[0].total, 301);
  assert.ok(mixedItems.every((d) => d.params.kind === "extra-close"));
});

test("消息延后组装：formatMessage 调用次数不超过返回条目数（≤ limit+1）", () => {
  let calls = 0;
  const counting = (rule) => {
    calls += 1;
    return rule;
  };
  const doc = Array.from({ length: 5000 }, () => "}").join("\n");
  const items = computeDiagnostics(doc, counting);
  assert.ok(
    calls <= DIAGNOSTICS_LIMIT + 1,
    `formatMessage 被调用 ${calls} 次，超过返回条目数`,
  );
  assert.equal(items.length, DIAGNOSTICS_LIMIT);
});

/* ── 边界输入（review-fix 补充） ─────────────────────────────────────────── */

test("边界输入：\\begin 缺参、\\begin{}、文末悬空反斜杠、BOM、CRLF 均不误报不崩溃", () => {
  // \begin 后无 {（缺参数）：按普通文本继续，不崩溃不误报
  assert.deepEqual(computeDiagnostics("\\begin\n\\end"), []);
  // 空环境名 \begin{}：整体吞掉
  assert.deepEqual(computeDiagnostics("\\begin{}x\\end{}"), []);
  // 文档末尾悬空反斜杠
  assert.deepEqual(computeDiagnostics("text\\"), []);
  // BOM 开头
  assert.deepEqual(computeDiagnostics("\uFEFF\\begin{a}x\\end{a}"), []);
  // CRLF 行尾：注释/命令扫描不受 \r 干扰
  assert.deepEqual(
    computeDiagnostics("% comment\r\n\\begin{a}x\\end{a}\r\n"),
    [],
  );
  // CRLF 的未闭合环境行号统计正确（按 \n 计行）
  const crlf = computeDiagnostics("\\begin{a}\r\nx\r\n");
  assert.deepEqual(brief(crlf), [[ENV, "unclosed", "a", 1]]);
});

test("截断边界：恰好 200 条不标注，201 条标注 total=201", () => {
  const exact = Array.from({ length: 200 }, () => "}").join("\n");
  const exactItems = computeDiagnostics(exact);
  assert.equal(exactItems.length, 200);
  assert.equal(exactItems[0].truncated, undefined);
  const over = Array.from({ length: 201 }, () => "}").join("\n");
  const overItems = computeDiagnostics(over);
  assert.equal(overItems.length, 200);
  assert.equal(overItems[0].truncated, true);
  assert.equal(overItems[0].total, 201);
});
