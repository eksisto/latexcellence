/**
 * 编辑器偏好纯模块（editor-plan §9 editorPreferences 切片、§11 偏好导入/导出）。
 *
 * 职责：
 *   1. 偏好默认值（决策 Q6：autoSave 默认关；§4：softWrap 默认开）；
 *   2. 字段白名单 + 类型校验：persist 水合、导入通道共用同一清洗入口——
 *      非法字段静默丢弃、合法字段立即生效，避免脏数据进 store；
 *   3. 导出/导入载荷（§11）：kind/version/prefs 结构，version 预留扩展
 *      （决策 Q19），导入按 version + 白名单严格校验（决策 Q20/Q21）。
 *
 * 纯函数，供 node --test 直接单测。
 */

export const EDITOR_PREFERENCES_DEFAULTS = Object.freeze({
  /** 自动保存：默认关闭（决策 Q6；行为联动属阶段四） */
  autoSave: false,
  /** 软换行：默认开启（LaTeX 源码长行常见，方案 §4） */
  softWrap: true,
  /** \begin{env} 自动补 \end：默认开启（editor v2 决策 Q18，阶段二） */
  autoCloseEnv: true,
  /** 编辑器字号（px） */
  fontSize: 14,
});

/** 导出载荷 kind 标识（§11；防止把任意 JSON 误当偏好导入） */
export const EDITOR_PREFERENCES_EXPORT_KIND =
  "latexcellence-editor-preferences";

/** 当前支持的导出版本（version 字段预留扩展，决策 Q19） */
export const EDITOR_PREFERENCES_EXPORT_VERSION = 1;

/** 偏好字段白名单（导出/导入校验同源） */
const PREFERENCE_KEYS = Object.keys(EDITOR_PREFERENCES_DEFAULTS);

const FONT_SIZE_MIN = 10;
const FONT_SIZE_MAX = 24;

function isBoolean(value) {
  return typeof value === "boolean";
}

/**
 * 校验字号：整数且在允许区间内。
 * @param {unknown} value
 * @returns {boolean}
 */
export function isValidFontSize(value) {
  return (
    Number.isInteger(value) && value >= FONT_SIZE_MIN && value <= FONT_SIZE_MAX
  );
}

/**
 * 按字段白名单清洗偏好输入：仅接受类型合法的字段，其余静默丢弃；
 * 输出整体覆盖当前切片（不合并未知键）。
 * @param {Record<string, unknown>|null|undefined} input 待清洗的偏好对象
 * @returns {{autoSave: boolean, softWrap: boolean, autoCloseEnv: boolean, fontSize: number}}
 */
export function sanitizePreferences(input) {
  if (!input || typeof input !== "object") {
    return { ...EDITOR_PREFERENCES_DEFAULTS };
  }
  const result = {};
  for (const key of PREFERENCE_KEYS) {
    const value = input[key];
    if (key === "fontSize") {
      if (isValidFontSize(value)) result[key] = value;
      else result[key] = EDITOR_PREFERENCES_DEFAULTS.fontSize;
      continue;
    }
    if (isBoolean(value)) result[key] = value;
    else result[key] = EDITOR_PREFERENCES_DEFAULTS[key];
  }
  return result;
}

/* ── 导出 / 导入（editor-plan §11，阶段四） ─────────────────────────────── */

/**
 * 序列化偏好为导出载荷文本（pretty-print JSON，§11）。
 * @param {{autoSave: boolean, softWrap: boolean, autoCloseEnv: boolean, fontSize: number}} prefs
 * @returns {string}
 */
export function serializePreferences(prefs) {
  const payload = {
    kind: EDITOR_PREFERENCES_EXPORT_KIND,
    version: EDITOR_PREFERENCES_EXPORT_VERSION,
    prefs: sanitizePreferences(prefs),
  };
  return `${JSON.stringify(payload, null, 2)}\n`;
}

/**
 * 解析并校验导入文本（文件或粘贴双通道共用，决策 Q20）。
 *
 * 校验规则（严格模式）：
 *   - JSON 解析失败 → { ok:false, reason:"invalid_json" }
 *   - kind 不匹配 → { ok:false, reason:"invalid_kind" }
 *   - version 缺失 / 非整数 / 高于当前支持版本 → unsupported_version
 *     （低版本向后兼容放行，字段按当前白名单清洗）
 *   - prefs 非对象 → invalid_payload
 *   - prefs 内非法字段经 sanitizePreferences 静默丢弃（§11）
 *
 * @param {string} text 用户提供的 JSON 文本
 * @returns {{ok: true, prefs: {autoSave: boolean, softWrap: boolean, autoCloseEnv: boolean, fontSize: number}}
 *           | {ok: false, reason: "invalid_json"|"invalid_kind"|"unsupported_version"|"invalid_payload"}}
 */
export function parsePreferencesImport(text) {
  let data;
  try {
    data = JSON.parse(text);
  } catch {
    return { ok: false, reason: "invalid_json" };
  }
  if (!data || typeof data !== "object" || Array.isArray(data)) {
    return { ok: false, reason: "invalid_json" };
  }
  if (data.kind !== EDITOR_PREFERENCES_EXPORT_KIND) {
    return { ok: false, reason: "invalid_kind" };
  }
  const version = data.version;
  if (
    !Number.isInteger(version) ||
    version < 1 ||
    version > EDITOR_PREFERENCES_EXPORT_VERSION
  ) {
    return { ok: false, reason: "unsupported_version" };
  }
  if (
    !data.prefs ||
    typeof data.prefs !== "object" ||
    Array.isArray(data.prefs)
  ) {
    return { ok: false, reason: "invalid_payload" };
  }
  // 合法字段整体覆盖切片：缺失字段回落默认值，未知/非法字段静默丢弃
  return { ok: true, prefs: sanitizePreferences(data.prefs) };
}
