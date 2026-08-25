import test from "node:test";
import assert from "node:assert/strict";

import {
  EDITOR_PREFERENCES_DEFAULTS,
  isValidFontSize,
  sanitizePreferences,
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
