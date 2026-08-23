/**
 * errorMessages 模式匹配核心单测
 *
 * errorMessages.js 顶层 import @/i18n（拉起 i18next 运行时 + 浏览器
 * LanguageDetector），无法在 node --test 下干净导入；因此 PATTERN_MAP 与
 * 模式匹配核心抽取在纯模块 ./errorPatterns.js 中，这里直接对其单测。
 * EXACT_MAP 精确匹配留在 errorMessages.js（frontend/test/
 * multiUserLocales.test.mjs 以源码文本解析它校验三语覆盖），其 i18n 插值
 * 依赖的三语占位符完整性在末尾用例中静态校验。
 */

import { test } from "node:test";
import assert from "node:assert/strict";

import zh from "../i18n/locales/zh.json" with { type: "json" };
import zhTW from "../i18n/locales/zh-TW.json" with { type: "json" };
import en from "../i18n/locales/en.json" with { type: "json" };

import { PATTERN_MAP, matchPattern } from "./errorPatterns.js";

// ── 缺陷回归：SSE 逐段检查路径的 "{文件名} P{段号}: " 前缀 ─────────

test("带文件前缀的 LLM 限流消息命中 llmRateLimited 并提取 retry_after", () => {
  // 后端 base_paragraph_check.py SSE 路径：f"{文件名} P{段落号}: {e}"
  const matched = matchPattern(
    "main.tex P3: LLM rate limit exceeded (21/20 per 60s); retry after 42s",
  );
  assert.ok(matched, "带前缀的限流消息必须命中模式表");
  assert.equal(matched.key, "llmRateLimited");
  assert.equal(matched.params.retry_after, "42");
});

test("无前缀的 LLM 限流消息（结构化 detail.message 路径）仍命中", () => {
  const matched = matchPattern(
    "LLM rate limit exceeded (5/10 per 60s); retry after 7s",
  );
  assert.ok(matched);
  assert.equal(matched.key, "llmRateLimited");
  assert.equal(matched.params.retry_after, "7");
});

test("带文件前缀的并发上限消息命中 llmConcurrencyLimited", () => {
  const matched = matchPattern(
    "chapter/main.tex P12: LLM concurrency limit reached (4/4); retry after 30s",
  );
  assert.ok(matched);
  assert.equal(matched.key, "llmConcurrencyLimited");
  assert.equal(matched.params.retry_after, "30");
});

test("多窗口 / 多位数字配额均能正确提取", () => {
  const rate = matchPattern(
    "notes.tex P1: LLM rate limit exceeded (100/50 per 3600s); retry after 600s",
  );
  assert.ok(rate);
  assert.equal(rate.params.retry_after, "600");

  const concurrency = matchPattern(
    "LLM concurrency limit reached (12/8); retry after 120s",
  );
  assert.ok(concurrency);
  assert.equal(concurrency.params.retry_after, "120");
});

// ── 与其他限流条目的区分（去锚定不得引入误伤）────────────────────

test("登录 / Web / Tavily 等其他限流措辞不被任何模式误命中", () => {
  // 这些走 errorMessages.js 的 EXACT_MAP 精确匹配（先于模式匹配执行，
  // 见 multiUserLocales.test.mjs 对 EXACT_MAP 的覆盖校验）；
  // 去锚定后的 LLM 治理模式不得在模式层吞掉它们。
  assert.equal(matchPattern("rate limited"), null);
  assert.equal(matchPattern("rate_limited"), null);
  assert.equal(
    matchPattern("Tavily keyless rate limit exceeded (HTTP 429); falling back"),
    null,
  );
});

test("治理错误码本身不落入模式表", () => {
  // 结构化路径由 EXACT_MAP[llm_rate_limited] 直接翻译，不经模式匹配
  assert.equal(matchPattern("llm_rate_limited"), null);
});

// ── 无法匹配时返回 null（translateError 层原样回显）────────────────

test("无关英文消息返回 null", () => {
  assert.equal(matchPattern("Something went wrong"), null);
  assert.equal(matchPattern(""), null);
});

test("格式不符的近似消息返回 null", () => {
  // 非数字配额 / 缺少 retry after 段，不得放行
  assert.equal(matchPattern("LLM rate limit exceeded (abc/def per xs)"), null);
  assert.equal(matchPattern("LLM concurrency limit reached (1/2)"), null);
});

// ── 映射表结构与三语占位符完整性 ─────────────────────────────────

test("PATTERN_MAP 条目均为 [regex, key, getParams] 三元组", () => {
  assert.ok(PATTERN_MAP.length > 0);
  for (const [regex, key, getParams] of PATTERN_MAP) {
    assert.ok(regex instanceof RegExp);
    assert.equal(typeof key, "string");
    assert.equal(typeof getParams, "function");
  }
});

test("三语 errors 命名空间包含 LLM 治理条目且 {{retry_after}} 占位符齐全", () => {
  const locales = [
    ["zh", zh],
    ["zh-TW", zhTW],
    ["en", en],
  ];
  for (const [name, resources] of locales) {
    const errors = resources.errors;
    for (const key of ["llmRateLimited", "llmConcurrencyLimited"]) {
      assert.ok(
        errors[key] && errors[key].includes("{{retry_after}}"),
        `${name}.errors.${key} 必须存在且含 {{retry_after}} 占位符`,
      );
    }
  }
});
