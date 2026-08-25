import test from "node:test";
import assert from "node:assert/strict";

import {
  EDITOR_PREFERENCES_DEFAULTS,
  EDITOR_PREFERENCES_EXPORT_KIND,
  EDITOR_PREFERENCES_EXPORT_VERSION,
  isValidFontSize,
  parsePreferencesImport,
  sanitizePreferences,
  serializePreferences,
} from "../src/lib/editor/preferences.js";

test("默认偏好符合方案决策（autoSave 关、softWrap 开）", () => {
  assert.deepEqual(EDITOR_PREFERENCES_DEFAULTS, {
    autoSave: false,
    softWrap: true,
    fontSize: 14,
  });
});

test("sanitizePreferences 仅接受类型合法的字段", () => {
  const cleaned = sanitizePreferences({
    autoSave: true,
    softWrap: false,
    fontSize: 18,
    evilField: "x",
  });
  assert.deepEqual(cleaned, { autoSave: true, softWrap: false, fontSize: 18 });
  assert.equal("evilField" in cleaned, false);
});

test("sanitizePreferences 对非法值回落默认值而非丢弃整个对象", () => {
  assert.deepEqual(
    sanitizePreferences({ autoSave: "yes", softWrap: 1, fontSize: "big" }),
    EDITOR_PREFERENCES_DEFAULTS,
  );
  // 合法与非法字段混合时逐字段裁决
  assert.deepEqual(sanitizePreferences({ autoSave: true, softWrap: null }), {
    autoSave: true,
    softWrap: true,
    fontSize: 14,
  });
});

test("sanitizePreferences 对空入参返回默认偏好", () => {
  assert.deepEqual(sanitizePreferences(null), EDITOR_PREFERENCES_DEFAULTS);
  assert.deepEqual(sanitizePreferences(undefined), EDITOR_PREFERENCES_DEFAULTS);
  assert.deepEqual(sanitizePreferences({}), EDITOR_PREFERENCES_DEFAULTS);
});

test("isValidFontSize 接受区间内整数并拒绝越界与小数", () => {
  assert.equal(isValidFontSize(10), true);
  assert.equal(isValidFontSize(14), true);
  assert.equal(isValidFontSize(24), true);
  assert.equal(isValidFontSize(9), false);
  assert.equal(isValidFontSize(25), false);
  assert.equal(isValidFontSize(13.5), false);
  assert.equal(isValidFontSize("14"), false);
});

/* ── 偏好导入 / 导出（editor-plan §11，阶段四） ───────────────────────────── */

test("serializePreferences 输出 pretty-print 的 kind/version/prefs 载荷", () => {
  const text = serializePreferences({
    autoSave: true,
    softWrap: false,
    fontSize: 18,
  });
  const parsed = JSON.parse(text);
  assert.deepEqual(parsed, {
    kind: EDITOR_PREFERENCES_EXPORT_KIND,
    version: EDITOR_PREFERENCES_EXPORT_VERSION,
    prefs: { autoSave: true, softWrap: false, fontSize: 18 },
  });
  // pretty-print：多行 + 2 空格缩进
  assert.ok(text.includes("\n"));
  assert.ok(text.includes('  "prefs"'));
});

test("serializePreferences 对非法偏好先清洗再导出（往返一致）", () => {
  const text = serializePreferences({ autoSave: "oops", fontSize: 9999 });
  const result = parsePreferencesImport(text);
  assert.deepEqual(result, {
    ok: true,
    prefs: EDITOR_PREFERENCES_DEFAULTS,
  });
});

test("parsePreferencesImport 接受合法导出载荷", () => {
  const text = serializePreferences({
    autoSave: false,
    softWrap: true,
    fontSize: 16,
  });
  const result = parsePreferencesImport(text);
  assert.deepEqual(result, {
    ok: true,
    prefs: { autoSave: false, softWrap: true, fontSize: 16 },
  });
});

test("parsePreferencesImport 拒绝非 JSON 文本与非对象载荷", () => {
  assert.equal(parsePreferencesImport("not json").ok, false);
  assert.equal(parsePreferencesImport("[1,2]").reason, "invalid_json");
  assert.equal(parsePreferencesImport("").reason, "invalid_json");
});

test("parsePreferencesImport 拒绝 kind 不匹配的载荷", () => {
  const result = parsePreferencesImport(
    JSON.stringify({ kind: "something-else", version: 1, prefs: {} }),
  );
  assert.equal(result.reason, "invalid_kind");
});

test("parsePreferencesImport 拒绝缺失或超前的版本号（version 预留扩展）", () => {
  assert.equal(
    parsePreferencesImport(
      JSON.stringify({ kind: EDITOR_PREFERENCES_EXPORT_KIND, prefs: {} }),
    ).reason,
    "unsupported_version",
  );
  assert.equal(
    parsePreferencesImport(
      JSON.stringify({
        kind: EDITOR_PREFERENCES_EXPORT_KIND,
        version: EDITOR_PREFERENCES_EXPORT_VERSION + 1,
        prefs: {},
      }),
    ).reason,
    "unsupported_version",
  );
  assert.equal(
    parsePreferencesImport(
      JSON.stringify({
        kind: EDITOR_PREFERENCES_EXPORT_KIND,
        version: "1",
        prefs: {},
      }),
    ).reason,
    "unsupported_version",
  );
});

test("parsePreferencesImport 拒绝 prefs 非对象的载荷", () => {
  const result = parsePreferencesImport(
    JSON.stringify({
      kind: EDITOR_PREFERENCES_EXPORT_KIND,
      version: EDITOR_PREFERENCES_EXPORT_VERSION,
      prefs: "nope",
    }),
  );
  assert.equal(result.reason, "invalid_payload");
});

test("parsePreferencesImport 静默丢弃非法字段、合法字段整体覆盖切片", () => {
  const result = parsePreferencesImport(
    JSON.stringify({
      kind: EDITOR_PREFERENCES_EXPORT_KIND,
      version: 1,
      prefs: {
        autoSave: true,
        fontSize: 20,
        evilField: "x",
        softWrap: "bogus", // 非法值 → 回落默认
      },
    }),
  );
  assert.equal(result.ok, true);
  // 整体覆盖语义：输出恰好是完整切片，未知键不进入结果
  assert.deepEqual(result.prefs, {
    autoSave: true,
    softWrap: true,
    fontSize: 20,
  });
});
