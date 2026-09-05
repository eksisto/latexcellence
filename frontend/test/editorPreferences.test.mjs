import test from "node:test";
import assert from "node:assert/strict";

import {
  CUSTOM_SNIPPETS_LIMIT,
  EDITOR_PREFERENCES_DEFAULTS,
  EDITOR_PREFERENCES_EXPORT_KIND,
  EDITOR_PREFERENCES_EXPORT_VERSION,
  isValidFontSize,
  parsePreferencesImport,
  sanitizeCustomSnippets,
  sanitizePreferences,
  serializePreferences,
} from "../src/lib/editor/preferences.js";

const snippet = {
  name: "myenv",
  insert: "\\begin{myenv}\n\t$1\n\\end{myenv}",
  zh: "我的环境",
  category: "custom",
};
/** 完整切片形状（默认值 + 覆盖项），避免逐字段罗列漂移 */
const fullPrefs = (overrides = {}) => ({
  autoSave: false,
  softWrap: true,
  autoCloseEnv: true,
  formulaHover: true,
  selectionMatches: true,
  fontSize: 14,
  customSnippets: [],
  ...overrides,
});

test("默认偏好符合方案决策（autoSave 关、softWrap 开、autoCloseEnv/formulaHover 开；v2 增 customSnippets）", () => {
  assert.deepEqual(EDITOR_PREFERENCES_DEFAULTS, fullPrefs());
  assert.equal(EDITOR_PREFERENCES_EXPORT_VERSION, 2);
});

test("sanitizePreferences 仅接受类型合法的字段", () => {
  const cleaned = sanitizePreferences({
    autoSave: true,
    softWrap: false,
    autoCloseEnv: false,
    formulaHover: false,
    fontSize: 18,
    evilField: "x",
  });
  assert.deepEqual(
    cleaned,
    fullPrefs({
      autoSave: true,
      softWrap: false,
      autoCloseEnv: false,
      formulaHover: false,
      fontSize: 18,
    }),
  );
  assert.equal("evilField" in cleaned, false);
});

test("sanitizePreferences 对非法值回落默认值而非丢弃整个对象", () => {
  assert.deepEqual(
    sanitizePreferences({ autoSave: "yes", softWrap: 1, fontSize: "big" }),
    EDITOR_PREFERENCES_DEFAULTS,
  );
  // 合法与非法字段混合时逐字段裁决
  assert.deepEqual(
    sanitizePreferences({ autoSave: true, softWrap: null }),
    fullPrefs({ autoSave: true }),
  );
});

test("sanitizePreferences 对空入参返回默认偏好", () => {
  assert.deepEqual(sanitizePreferences(null), EDITOR_PREFERENCES_DEFAULTS);
  assert.deepEqual(sanitizePreferences(undefined), EDITOR_PREFERENCES_DEFAULTS);
  assert.deepEqual(sanitizePreferences({}), EDITOR_PREFERENCES_DEFAULTS);
});

/* ── 自定义片段条目级清洗（editor v3 阶段二 / 决策 Q7） ─────────────────── */

test("sanitizePreferences 对 customSnippets 做条目级清洗并整体纳入切片", () => {
  const cleaned = sanitizePreferences({
    customSnippets: [
      snippet,
      { name: "\\bad", insert: "x", zh: "" }, // 名称非法 → 丢弃
      { name: "myenv", insert: "dup", zh: "" }, // 重名 → 先到先得
      "junk",
    ],
  });
  assert.deepEqual(cleaned.customSnippets, [snippet]);
});

test("sanitizePreferences 对非法 customSnippets 回落空数组", () => {
  assert.deepEqual(
    sanitizePreferences({ customSnippets: "nope" }).customSnippets,
    [],
  );
  assert.deepEqual(sanitizePreferences({}).customSnippets, []);
});

test("sanitizeCustomSnippets 数量上限为 100", () => {
  assert.equal(CUSTOM_SNIPPETS_LIMIT, 100);
  const many = Array.from({ length: 120 }, (_, i) => ({
    name: `s${i}`,
    insert: "x",
    zh: "",
  }));
  assert.equal(sanitizeCustomSnippets(many).length, 100);
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

test("serializePreferences 输出 pretty-print 的 kind/version/prefs 载荷（v2 含 customSnippets）", () => {
  const text = serializePreferences({
    autoSave: true,
    softWrap: false,
    autoCloseEnv: false,
    formulaHover: false,
    fontSize: 18,
  });
  const parsed = JSON.parse(text);
  assert.deepEqual(parsed, {
    kind: EDITOR_PREFERENCES_EXPORT_KIND,
    version: EDITOR_PREFERENCES_EXPORT_VERSION,
    prefs: {
      autoSave: true,
      softWrap: false,
      autoCloseEnv: false,
      formulaHover: false,
      selectionMatches: true,
      fontSize: 18,
      customSnippets: [],
    },
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

test("serializePreferences 导出后导入自定义片段原样往返（清洗白名单内）", () => {
  const text = serializePreferences({ customSnippets: [snippet] });
  const result = parsePreferencesImport(text);
  assert.equal(result.ok, true);
  assert.deepEqual(result.prefs.customSnippets, [snippet]);
});

test("parsePreferencesImport 接受合法导出载荷", () => {
  const text = serializePreferences({
    autoSave: false,
    softWrap: true,
    autoCloseEnv: false,
    formulaHover: true,
    fontSize: 16,
  });
  const result = parsePreferencesImport(text);
  assert.deepEqual(result, {
    ok: true,
    prefs: {
      autoSave: false,
      softWrap: true,
      autoCloseEnv: false,
      formulaHover: true,
      selectionMatches: true,
      fontSize: 16,
      customSnippets: [],
    },
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
    autoCloseEnv: true,
    formulaHover: true,
    selectionMatches: true,
    fontSize: 20,
    customSnippets: [],
  });
});

/* ── v1 / v2 载荷兼容（editor v3 阶段二 / 决策 Q7） ───────────────────────── */

test("parsePreferencesImport 接受 v1 载荷（无 customSnippets 字段）且不丢数据（决策 Q7）", () => {
  // v1 时代导出的真实载荷形状：version 1，prefs 无片段字段
  const v1Payload = {
    kind: EDITOR_PREFERENCES_EXPORT_KIND,
    version: 1,
    prefs: {
      autoSave: true,
      softWrap: false,
      autoCloseEnv: false,
      formulaHover: false,
      selectionMatches: false,
      fontSize: 18,
    },
  };
  const result = parsePreferencesImport(JSON.stringify(v1Payload));
  assert.equal(result.ok, true);
  assert.deepEqual(result.prefs, {
    autoSave: true,
    softWrap: false,
    autoCloseEnv: false,
    formulaHover: false,
    selectionMatches: false,
    fontSize: 18,
    customSnippets: [], // 片段字段缺失回落空数组，不视为非法
  });
});

test("parsePreferencesImport 接受 v2 载荷并清洗片段条目", () => {
  const result = parsePreferencesImport(
    JSON.stringify({
      kind: EDITOR_PREFERENCES_EXPORT_KIND,
      version: 2,
      prefs: {
        customSnippets: [snippet, { name: "bad name", insert: "x", zh: "" }],
      },
    }),
  );
  assert.equal(result.ok, true);
  assert.deepEqual(result.prefs.customSnippets, [snippet]);
  // 其余字段回落默认值（整体覆盖语义）
  assert.equal(result.prefs.fontSize, 14);
});

test("parsePreferencesImport 拒绝 v3（超当前版本）载荷", () => {
  const result = parsePreferencesImport(
    JSON.stringify({
      kind: EDITOR_PREFERENCES_EXPORT_KIND,
      version: EDITOR_PREFERENCES_EXPORT_VERSION + 1,
      prefs: { customSnippets: [snippet] },
    }),
  );
  assert.equal(result.reason, "unsupported_version");
});
