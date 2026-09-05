/**
 * 文档统计纯模块（editor-productivity-design 阶段八：底部状态栏，
 * 借鉴 Notepad++ 状态栏的 Ln/Col 与长度展示；字数口径面向论文写作）。
 *
 * countDocStats(text)：
 *   - lines：文本行数（与 CM6 doc.lines 口径一致，空文本为 1 行）；
 *   - cjkChars：CJK 汉字逐字计数（不含全角标点，对齐常见「字数」口径）；
 *   - latinWords：西文词计数（字母数字连续段，允许内部 '- / ’ 连字）；
 *   - totalWords = cjkChars + latinWords。
 * LaTeX 命令按原文近似统计（不做语法剥离——状态栏定位为粗略参考，
 * 精确统计应由编译产物承担）。
 *
 * createStatsHub：编辑器光标/文档统计的轻量发布-订阅 hub。LaTeXEditor
 * 在事务内 publish，EditorStatusBar 经 useSyncExternalStore 订阅——统计
 * 状态独立于页面 React 树，键入/移动光标只重渲染状态栏自身。快照按值
 * 浅比较去重（引用稳定，避免 useSyncExternalStore 无效刷新）。
 *
 * 纯函数/纯数据结构，供 node --test 直接单测。
 */

/** CJK 统一表意文字（含扩展 A 与兼容区，不含标点） */
const CJK_CHAR_RE = /[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]/g;

/** 西文词：字母数字连续段，允许内部 '- / ’ 连字 */
const LATIN_WORD_RE = /[A-Za-z0-9]+(?:['’-][A-Za-z0-9]+)*/g;

/**
 * @param {string} text 全文
 * @returns {{lines: number, cjkChars: number, latinWords: number, totalWords: number}}
 */
export function countDocStats(text) {
  const lines = typeof text === "string" ? text.split("\n").length : 1;
  const cjkChars =
    (typeof text === "string" ? text.match(CJK_CHAR_RE) : null)?.length ?? 0;
  const latinWords =
    (typeof text === "string" ? text.match(LATIN_WORD_RE) : null)?.length ?? 0;
  return { lines, cjkChars, latinWords, totalWords: cjkChars + latinWords };
}

/** 状态栏快照形状（全数值，浅比较即可判等） */
const STATS_KEYS = ["line", "col", "selectedChars", "lines", "words"];

/**
 * @returns {{
 *   publish: (stats: object) => void,
 *   subscribe: (listener: () => void) => () => void,
 *   getSnapshot: () => object|null,
 * }}
 */
export function createStatsHub() {
  let snapshot = null;
  const listeners = new Set();
  return {
    publish(next) {
      if (statsEqual(snapshot, next)) return;
      snapshot = next;
      for (const listener of listeners) listener();
    },
    subscribe(listener) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    getSnapshot: () => snapshot,
  };
}

function statsEqual(a, b) {
  if (a === b) return true;
  if (!a || !b) return false;
  for (const key of STATS_KEYS) {
    if (a[key] !== b[key]) return false;
  }
  return true;
}
