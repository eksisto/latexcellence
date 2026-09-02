/**
 * 引用补全符号模块的 node --test（无 DOM）：
 *   - 项目缓存键归约与 createProjectSymbolCache（在途共享 / 失效重拉 /
 *     失败出缓存重试）；
 *   - buildLabelOptions / buildCiteOptions 的 option 映射（detail 出处、
 *     type 区分）与 filterCompletionOptions 的过滤排序；
 *   - makeReferenceCompletionSource 三路触发（\ref{ / \label{ / \cite{，
 *     含可选参数）与替换区间契约（只替换 `{` 之后的部分 key）。
 */

import test from "node:test";
import assert from "node:assert/strict";

import { EditorState } from "@codemirror/state";
import { CompletionContext } from "@codemirror/autocomplete";

import {
  buildCiteOptions,
  buildLabelOptions,
  createProjectSymbolCache,
  filterCompletionOptions,
  reduceProjectKey,
} from "../src/lib/editor/latexSymbols.js";
import { makeReferenceCompletionSource } from "../src/lib/editor/latexCompletion.js";

const SYMBOL_DATA = {
  labels: [
    { key: "sec:intro", file_path: "main.tex", line: 3 },
    { key: "sec:method", file_path: "chapters/method.tex", line: 2 },
    { key: "eq:euler", file_path: "chapters/method.tex", line: 10 },
  ],
  cites: [
    {
      key: "smith2020",
      file_path: "refs.bib",
      kind: "bib",
      title: "A Study of Latex",
    },
    { key: "doe2021", file_path: "main.tex", kind: "tex" },
  ],
};

/* ── 项目缓存 ────────────────────────────────────────────────────────── */

test("reduceProjectKey：单用户 projectPath 优先，多用户回落 projectId", () => {
  assert.equal(
    reduceProjectKey({ projectPath: "/tmp/proj", projectId: "p1" }),
    "/tmp/proj",
  );
  assert.equal(reduceProjectKey({ projectId: "p1" }), "p1");
  assert.equal(reduceProjectKey({}), null);
  assert.equal(reduceProjectKey(), null);
});

test("符号缓存：同项目共享在途 Promise，只拉取一次", async () => {
  const cache = createProjectSymbolCache();
  let calls = 0;
  const fetcher = () => {
    calls += 1;
    return Promise.resolve(SYMBOL_DATA);
  };
  const [a, b] = await Promise.all([
    cache.load("proj-a", fetcher),
    cache.load("proj-a", fetcher),
  ]);
  assert.equal(calls, 1);
  assert.equal(a, SYMBOL_DATA);
  assert.equal(b, SYMBOL_DATA);
  // 不同项目互不串缓存
  await cache.load("proj-b", fetcher);
  assert.equal(calls, 2);
  assert.equal(cache.size, 2);
});

test("符号缓存：invalidate 后重拉，clear 清空全部", async () => {
  const cache = createProjectSymbolCache();
  let calls = 0;
  const fetcher = () => {
    calls += 1;
    return Promise.resolve(SYMBOL_DATA);
  };
  await cache.load("proj", fetcher);
  cache.invalidate("proj");
  await cache.load("proj", fetcher);
  assert.equal(calls, 2);
  cache.clear();
  assert.equal(cache.size, 0);
});

test("符号缓存：拉取失败静默返回 null 并出缓存，下次触发重试", async () => {
  const cache = createProjectSymbolCache();
  let calls = 0;
  const fetcher = () => {
    calls += 1;
    return Promise.reject(new Error("network down"));
  };
  assert.equal(await cache.load("proj", fetcher), null);
  assert.equal(await cache.load("proj", fetcher), null);
  assert.equal(calls, 2, "失败的拉取不得常驻缓存");
});

test("符号缓存：项目键缺失时不发起拉取", async () => {
  const cache = createProjectSymbolCache();
  let calls = 0;
  const result = await cache.load(null, () => {
    calls += 1;
    return Promise.resolve(SYMBOL_DATA);
  });
  assert.equal(result, null);
  assert.equal(calls, 0);
});

/* ── option 映射与过滤 ───────────────────────────────────────────────── */

test("label option：detail 为文件:行号，type 区分为 label", () => {
  const options = buildLabelOptions(SYMBOL_DATA);
  assert.equal(options.length, 3);
  const intro = options.find((o) => o.label === "sec:intro");
  assert.equal(intro.detail, "main.tex:3");
  assert.equal(intro.type, "label");
});

test("cite option：bib 条目 detail 取标题，tex 已引用 key 取文件出处", () => {
  const options = buildCiteOptions(SYMBOL_DATA);
  const bib = options.find((o) => o.label === "smith2020" && o.boost === 1);
  assert.equal(bib.detail, "A Study of Latex");
  const tex = options.find((o) => o.label === "doe2021");
  assert.equal(tex.detail, "main.tex");
  assert.equal(tex.type, "cite");
});

test("cite option：bib 标题缺失时 detail 回落文件路径", () => {
  const options = buildCiteOptions({
    cites: [{ key: "k1", file_path: "refs.bib", kind: "bib", title: "" }],
  });
  assert.equal(options[0].detail, "refs.bib");
});

test("过滤：前缀命中优先、子串其次、无关项剔除", () => {
  const options = buildLabelOptions(SYMBOL_DATA);
  const filtered = filterCompletionOptions(options, "sec");
  assert.deepEqual(
    filtered.map((o) => o.label),
    ["sec:intro", "sec:method"],
  );
  const partial = filterCompletionOptions(options, "intro");
  assert.deepEqual(
    partial.map((o) => o.label),
    ["sec:intro"],
  );
  // 空查询给全量
  assert.equal(filterCompletionOptions(options, "").length, 3);
});

test("过滤：大小写不敏感", () => {
  const filtered = filterCompletionOptions(
    buildCiteOptions(SYMBOL_DATA),
    "SMITH",
  );
  assert.ok(filtered.some((o) => o.label === "smith2020"));
});

/* ── 引用补全 source ─────────────────────────────────────────────────── */

function runSource(source, docText) {
  const state = EditorState.create({ doc: docText });
  return source(new CompletionContext(state, docText.length, true));
}

test("引用 source：\\ref{ 触发 label 补全，from 在 { 之后", async () => {
  const source = makeReferenceCompletionSource(() =>
    Promise.resolve(SYMBOL_DATA),
  );
  const doc = "\\ref{sec";
  const result = await runSource(source, doc);
  assert.ok(result, "应产生补全结果");
  assert.equal(result.from, 5, "替换区间应只覆盖 { 之后的部分 key");
  const labels = result.options.map((o) => o.label);
  assert.deepEqual(labels, ["sec:intro", "sec:method"]);
  // validFor 覆盖 key 字符集，弹层存续期间继续敲 key 不重复查询
  assert.ok(result.validFor.test("aB_9:x-.z"));
  assert.ok(!result.validFor.test("{"));
});

test("引用 source：\\label{ 同样补全已有 label（避免重复定义）", async () => {
  const source = makeReferenceCompletionSource(() =>
    Promise.resolve(SYMBOL_DATA),
  );
  const result = await runSource(source, "\\label{eq:eu");
  assert.ok(result);
  assert.deepEqual(
    result.options.map((o) => o.label),
    ["eq:euler"],
  );
});

test("引用 source：cleveref 的 \\cref{ 同样触发 label 补全", async () => {
  const source = makeReferenceCompletionSource(() =>
    Promise.resolve(SYMBOL_DATA),
  );
  const result = await runSource(source, "\\cref{sec");
  assert.ok(result, "\\cref 应与 \\Cref 一样触发");
  assert.deepEqual(
    result.options.map((o) => o.label),
    ["sec:intro", "sec:method"],
  );
});

test("引用 source：\\cite 可选参数与部分 key 触发 bib 补全", async () => {
  const source = makeReferenceCompletionSource(() =>
    Promise.resolve(SYMBOL_DATA),
  );
  const doc = "\\cite[p.~5]{smi";
  const result = await runSource(source, doc);
  assert.ok(result, "带可选参数的 \\cite 应触发");
  assert.deepEqual(
    result.options.map((o) => o.label),
    ["smith2020"],
  );
});

test("引用 source：非触发前缀返回 null", async () => {
  const source = makeReferenceCompletionSource(() =>
    Promise.resolve(SYMBOL_DATA),
  );
  assert.equal(await runSource(source, "\\begin{it"), null);
  assert.equal(await runSource(source, "sec:intro"), null);
  // 刚敲完 \ref{ 的空查询给全量候选
  const empty = await runSource(source, "\\ref{");
  assert.ok(empty, "空查询应返回候选列表");
  assert.equal(empty.from, 5);
});

test("引用 source：符号数据缺失（项目未打开/拉取失败）时短路", async () => {
  const source = makeReferenceCompletionSource(() => Promise.resolve(null));
  assert.equal(await runSource(source, "\\ref{sec"), null);
});
