/**
 * 编辑器偏好纯模块（editor-design §9 editorPreferences 切片、§11 偏好导入/导出；
 * editor v3 阶段二 / 决策 Q7：customSnippets 并入偏好 persist，version 1 → 2）。
 *
 * 职责：
 *   1. 偏好默认值（决策 Q6：autoSave 默认关；§4：softWrap 默认开）；
 *   2. 字段白名单 + 类型校验：persist 水合、导入通道共用同一清洗入口——
 *      非法字段静默丢弃、合法字段立即生效，避免脏数据进 store；
 *      customSnippets 数组另做条目级清洗（schema 校验、去重名、超限截断）；
 *   3. 导出/导入载荷（§11）：kind/version/prefs 结构，version 预留扩展
 *      （决策 Q19），导入按 version + 白名单严格校验（决策 Q20/Q21）；
 *      v2 起载荷携带 customSnippets，v1 载荷（无片段）向后兼容放行。
 *
 * 纯函数（零外部依赖），供 node --test 直接单测。
 */

export const EDITOR_PREFERENCES_DEFAULTS = Object.freeze({
  /** 自动保存：默认关闭（决策 Q6；行为联动属阶段四） */
  autoSave: false,
  /** 软换行：默认开启（LaTeX 源码长行常见，方案 §4） */
  softWrap: true,
  /** \begin{env} 自动补 \end：默认开启（editor v2 决策 Q18，阶段二） */
  autoCloseEnv: true,
  /** KaTeX 公式悬停预览：默认开启（editor v2 决策 Q18/Q9，阶段六） */
  formulaHover: true,
  /** 选中即高亮全部匹配：默认开启（阶段八，借鉴 Notepad++ 智能高亮） */
  selectionMatches: true,
  /** 编辑器字号（px） */
  fontSize: 14,
  /** 用户自定义片段库（editor v3 阶段二 / 决策 Q7） */
  customSnippets: Object.freeze([]),
});

/** 导出载荷 kind 标识（§11；防止把任意 JSON 误当偏好导入） */
export const EDITOR_PREFERENCES_EXPORT_KIND =
  "latexcellence-editor-preferences";

/** 当前支持的导出版本（v2：载荷含 customSnippets；v1 载荷导入兼容，决策 Q7） */
export const EDITOR_PREFERENCES_EXPORT_VERSION = 2;

/** 偏好字段白名单（导出/导入校验同源） */
const PREFERENCE_KEYS = Object.keys(EDITOR_PREFERENCES_DEFAULTS);

const FONT_SIZE_MIN = 10;
const FONT_SIZE_MAX = 24;

/* ── 自定义片段条目清洗（editor v3 阶段二 / 决策 Q7） ─────────────────────── */

/** 自定义片段数量上限（超出截断，先到先得） */
export const CUSTOM_SNIPPETS_LIMIT = 100;
/** 单条插入模板长度上限（超出整条丢弃——截断可能产生残缺 LaTeX） */
export const CUSTOM_SNIPPET_INSERT_MAX = 4000;
/** 命令名约束（与命令数据库 / wrap 模块的 name 校验同规则） */
const CUSTOM_SNIPPET_NAME_RE = /^[A-Za-z][A-Za-z0-9*]*$/;

/**
 * 清洗单个自定义片段条目：仅接受 { name, insert, zh } 形状，category 恒为
 * "custom"（用户不可自选类目）、其余字段（grid/pkg 等）一律剥离——运行期
 * 字段由消费侧合并层（customCommands.normalizeCustomSnippet）派生。
 * name 去首尾空白后须匹配命令名规则；insert 非空且不超长；zh 非字符串
 * 回落空串（允许留空，中文名仅影响中文/拼音检索路）。非法条目返回 null。
 * @param {unknown} value
 * @returns {{name: string, insert: string, zh: string, category: "custom"}|null}
 */
export function sanitizeCustomSnippet(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const name = typeof value.name === "string" ? value.name.trim() : "";
  if (!CUSTOM_SNIPPET_NAME_RE.test(name)) return null;
  const insert = typeof value.insert === "string" ? value.insert : "";
  if (insert.length === 0 || insert.length > CUSTOM_SNIPPET_INSERT_MAX) {
    return null;
  }
  const zh = typeof value.zh === "string" ? value.zh.trim() : "";
  return { name, insert, zh, category: "custom" };
}

/**
 * 清洗自定义片段数组：逐条 sanitizeCustomSnippet，同名去重（先到先得），
 * 超过 CUSTOM_SNIPPETS_LIMIT 截断。非数组入参回落空数组。
 * @param {unknown} value
 * @returns {{name: string, insert: string, zh: string, category: "custom"}[]}
 */
export function sanitizeCustomSnippets(value) {
  if (!Array.isArray(value)) return [];
  const result = [];
  const seen = new Set();
  for (const item of value) {
    if (result.length >= CUSTOM_SNIPPETS_LIMIT) break;
    const entry = sanitizeCustomSnippet(item);
    if (!entry || seen.has(entry.name)) continue;
    seen.add(entry.name);
    result.push(entry);
  }
  return result;
}

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
 * 输出整体覆盖当前切片（不合并未知键）。customSnippets 数组逐条目级清洗
 * （schema 校验、去重名、超限截断，见 sanitizeCustomSnippets）。
 * @param {Record<string, unknown>|null|undefined} input 待清洗的偏好对象
 * @returns {{autoSave: boolean, softWrap: boolean, autoCloseEnv: boolean, formulaHover: boolean, selectionMatches: boolean, fontSize: number, customSnippets: Array}}
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
    if (key === "customSnippets") {
      result[key] = sanitizeCustomSnippets(value);
      continue;
    }
    if (isBoolean(value)) result[key] = value;
    else result[key] = EDITOR_PREFERENCES_DEFAULTS[key];
  }
  return result;
}

/* ── 导出 / 导入（editor-design §11，阶段四） ─────────────────────────────── */

/**
 * 序列化偏好为导出载荷文本（pretty-print JSON，§11）。v2 载荷携带
 * customSnippets（决策 Q7），随既有导入导出双通道分发。
 * @param {{autoSave: boolean, softWrap: boolean, autoCloseEnv: boolean, formulaHover: boolean, selectionMatches: boolean, fontSize: number, customSnippets?: Array}} prefs
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
 *     （低版本向后兼容放行，字段按当前白名单清洗；决策 Q7：v1 载荷无
 *     customSnippets 字段，清洗后回落空数组，可导入不丢数据）
 *   - prefs 非对象 → invalid_payload
 *   - prefs 内非法字段经 sanitizePreferences 静默丢弃（§11）
 *
 * @param {string} text 用户提供的 JSON 文本
 * @returns {{ok: true, prefs: {autoSave: boolean, softWrap: boolean, autoCloseEnv: boolean, formulaHover: boolean, selectionMatches: boolean, fontSize: number, customSnippets: Array}}
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
