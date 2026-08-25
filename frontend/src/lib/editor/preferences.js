/**
 * 编辑器偏好纯模块（editor-plan §9 editorPreferences 切片、§11 偏好导入/导出）。
 *
 * 职责：
 *   1. 偏好默认值（决策 Q6：autoSave 默认关；§4：softWrap 默认开）；
 *   2. 字段白名单 + 类型校验：persist 水合与将来（阶段四）的导入通道共用
 *      同一清洗入口——非法字段静默丢弃、合法字段立即生效，避免脏数据进 store。
 *
 * 纯函数，供 node --test 直接单测；导入/导出 UI 属阶段四，本阶段只提供
 * 数据结构与最小设置入口。
 */

export const EDITOR_PREFERENCES_DEFAULTS = Object.freeze({
  /** 自动保存：默认关闭（决策 Q6；行为联动属阶段四） */
  autoSave: false,
  /** 软换行：默认开启（LaTeX 源码长行常见，方案 §4） */
  softWrap: true,
  /** 编辑器字号（px） */
  fontSize: 14,
});

/** 偏好字段白名单（导出/导入校验同源，version 扩展见方案 §11） */
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
 * @returns {{autoSave: boolean, softWrap: boolean, fontSize: number}}
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
