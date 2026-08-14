import test from "node:test";
import assert from "node:assert/strict";

import {
  appendTokenToUrl,
  normalizeApiBase,
  resolveUrlPath,
} from "../src/utils/apiUrl.js";

// ── normalizeApiBase ───────────────────────────────────────────────────

test("normalizeApiBase 去除末尾斜杠", () => {
  assert.equal(
    normalizeApiBase("http://localhost:8000/api///"),
    "http://localhost:8000/api",
  );
  assert.equal(normalizeApiBase("/api/"), "/api");
});

test("normalizeApiBase 空值回退到默认基地址", () => {
  assert.equal(normalizeApiBase(""), "/api");
  assert.equal(normalizeApiBase(null), "/api");
  assert.equal(
    normalizeApiBase("", "http://localhost:8000/api"),
    "http://localhost:8000/api",
  );
});

// ── appendTokenToUrl ───────────────────────────────────────────────────

test("appendTokenToUrl 无令牌时原样返回", () => {
  assert.equal(
    appendTokenToUrl("http://localhost:8000/api/x", ""),
    "http://localhost:8000/api/x",
  );
  assert.equal(
    appendTokenToUrl("http://localhost:8000/api/x", null),
    "http://localhost:8000/api/x",
  );
});

test("appendTokenToUrl 无查询参数时用 ?token=", () => {
  assert.equal(
    appendTokenToUrl("http://localhost:8000/api/x", "secret"),
    "http://localhost:8000/api/x?token=secret",
  );
});

test("appendTokenToUrl 已有查询参数时用 &token=", () => {
  assert.equal(
    appendTokenToUrl("http://localhost:8000/api/x?y=1", "secret"),
    "http://localhost:8000/api/x?y=1&token=secret",
  );
});

test("appendTokenToUrl 令牌特殊字符按 URL 编码", () => {
  assert.equal(
    appendTokenToUrl("http://localhost:8000/api/x", "a b&c=1"),
    "http://localhost:8000/api/x?token=a%20b%26c%3D1",
  );
});

// ── resolveUrlPath ─────────────────────────────────────────────────────

test("resolveUrlPath 绝对 /api 路径不重复拼接前缀", () => {
  assert.equal(
    resolveUrlPath(
      "/api/git-diff/pdf/abc",
      "http://localhost:8000/api",
      "http://localhost:8000",
    ),
    "http://localhost:8000/api/git-diff/pdf/abc",
  );
});

test("resolveUrlPath 相对路径按 API base 目录解析", () => {
  assert.equal(
    resolveUrlPath(
      "git-diff/pdf/abc",
      "http://localhost:8000/api",
      "http://localhost:8000",
    ),
    "http://localhost:8000/api/git-diff/pdf/abc",
  );
});

test("resolveUrlPath 相对 base 补上页面 origin", () => {
  assert.equal(
    resolveUrlPath("/api/git-diff/pdf/abc", "/api", "http://myserver:8000"),
    "http://myserver:8000/api/git-diff/pdf/abc",
  );
});

test("resolveUrlPath 第三方绝对 URL 原样返回（不泄漏令牌）", () => {
  assert.equal(
    resolveUrlPath(
      "https://arxiv.org/pdf/2401.12345",
      "http://localhost:8000/api",
      "http://localhost:8000",
    ),
    "https://arxiv.org/pdf/2401.12345",
  );
});

test("resolveUrlPath 保留已有查询参数", () => {
  assert.equal(
    resolveUrlPath(
      "/api/x?y=1",
      "http://localhost:8000/api",
      "http://localhost:8000",
    ),
    "http://localhost:8000/api/x?y=1",
  );
});

test("resolveUrlPath 空路径原样返回", () => {
  assert.equal(resolveUrlPath("", "http://localhost:8000/api", ""), "");
  assert.equal(resolveUrlPath(null, "http://localhost:8000/api", ""), null);
});

test("resolveUrlPath base 配置异常时退回拼接且不抛错", () => {
  assert.equal(
    resolveUrlPath("/api/x", "not a url", "http://localhost:8000"),
    "not a url/api/x",
  );
});
