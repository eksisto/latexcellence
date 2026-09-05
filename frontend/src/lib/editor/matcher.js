/**
 * 统一命令匹配器（editor-design §6.2 / 决策 Q8）。
 *
 * 四路匹配，行内补全与命令面板必须共用本文件保证行为一致：
 *   1. 中文子串：query 含于 zh（`阿尔法` → \alpha）；
 *   2. 全拼：加载时对 zh 预计算全拼序列（`aerfa`），勿每键重算；
 *   3. 首字母：预计算首字母序列（`ae`）；
 *   4. 英文前缀/子串：对 name 匹配（`alpha` / `al`）。
 *
 * 性能与体积（方案 §6.2 注意点）：
 *   - 拼音序列在索引构建时一次性算好并缓存（模块级 promise，只算一次），
 *     后续每次按键只做纯字符串比较；
 *   - pinyin-pro 经动态 `import()` 按需引入：只有真正构建索引（编辑器
 *     页面首次触发补全/面板）时才下载该 chunk，不进主包；
 *   - 数学专名的多音字误读经 PINYIN_WORD_FIXES 词级修正（customPinyin
 *     注册），在首次 pinyin() 调用前完成一次。
 */

import { LATEX_COMMANDS } from "../../data/latexCommands/index.js";

/* ── 得分体系：四路统一到 0-100 同一刻度后归一排序 ─────────────────────── */

export const SCORE = {
  /** 英文名精确相等 */
  ENGLISH_EXACT: 98,
  /** 中文精确相等 */
  ZH_EXACT: 100,
  /** 全拼整体相等 */
  PINYIN_EXACT: 96,
  /** 首字母序列完全相等 */
  INITIALS_EXACT: 94,
  /** 中文名前缀命中 */
  ZH_PREFIX: 90,
  /** 英文名前缀命中 */
  ENGLISH_PREFIX: 92,
  /** 全拼前缀命中 */
  PINYIN_PREFIX: 88,
  /** 首字母前缀命中 */
  INITIALS_PREFIX: 86,
  /** 中文名子串命中 */
  ZH_SUBSTRING: 80,
  /** 英文名子串命中 */
  ENGLISH_SUBSTRING: 78,
  /** 全拼子串命中 */
  PINYIN_SUBSTRING: 76,
  /** 首字母子串命中（低权重：中文习惯只输中心名词首字母，如 jz → 矩阵家族） */
  INITIALS_SUBSTRING: 72,
};

/** 单次搜索默认返回上限 */
export const DEFAULT_MATCH_LIMIT = 60;

const CJK_RE = /[\u4e00-\u9fff]/;

/**
 * 归一化查询串：去首尾空白、转小写、去内部空白。
 * 中文不受影响；英文查询大小写不敏感；拼音序列本身不含空白。
 * @param {string} rawQuery 用户输入
 * @returns {string}
 */
export function normalizeQuery(rawQuery) {
  if (typeof rawQuery !== "string") return "";
  return rawQuery.trim().toLowerCase().replace(/\s+/g, "");
}

/**
 * 构建匹配索引骨架：为每个条目登记原始顺序（稳定性 tie-break 用）。
 * 纯同步、无拼音依赖；拼音序列随后由 fillPinyinSequences 填充。
 * @param {Array} commands 命令条目数组
 */
export function buildCommandIndex(commands = LATEX_COMMANDS) {
  return (commands ?? []).map((entry, order) => ({
    entry,
    order,
    zh: typeof entry?.zh === "string" ? entry.zh : "",
    name: typeof entry?.name === "string" ? entry.name.toLowerCase() : "",
    fullPinyin: "",
    initials: "",
  }));
}

/**
 * 词级读音修正表：pinyin-pro 默认词典对多音字取最常用读音，数学专名会
 * 取错——「伽」默认 jiā（伽利略），伽马/欧米伽应读 gā，导致按 gama 搜
 * 不到 \gamma 反而 jiama 误命中。
 * 只注册词级（非字级）映射：不影响同字其他读音；且注册词可作用于包含
 * 它的更长条目（「大写伽马」「大写欧米伽」随词命中一并修正）。
 */
export const PINYIN_WORD_FIXES = {
  伽马: "ga ma",
  欧米伽: "ou mi ga",
};

/**
 * 用给定拼音转换函数填充全拼/首字母序列（仅中文部分参与匹配，
 * 非 a-z 字符剔除，避免标点/符号混入拼音序列）。
 * @param {Array} indexRows buildCommandIndex 的产物
 * @param {(text: string, opts: object) => string[] | string} pinyinFn pinyin-pro 的 pinyin()
 */
export function fillPinyinSequences(indexRows, pinyinFn) {
  for (const row of indexRows) {
    if (!row.zh || row.fullPinyin) continue;
    try {
      const full = pinyinFn(row.zh, {
        toneType: "none",
        type: "array",
        separator: "",
      });
      const initials = pinyinFn(row.zh, {
        pattern: "first",
        toneType: "none",
        type: "array",
        separator: "",
      });
      const toAscii = (arr) =>
        (Array.isArray(arr) ? arr.join("") : String(arr))
          .toLowerCase()
          .replace(/[^a-z]/g, "");
      row.fullPinyin = toAscii(full);
      row.initials = toAscii(initials);
    } catch {
      // 单条目转换失败不拖垮整个索引：留空即该条目两路不参与命中
      row.fullPinyin = "";
      row.initials = "";
    }
  }
  return indexRows;
}

/**
 * 单行四路打分：取所有命中路径的最高分；未命中返回 0。
 * @param {{zh:string,name:string,fullPinyin:string,initials:string}} row
 * @param {string} q normalizeQuery 之后的查询串
 * @returns {number} 0-100
 */
export function matchScore(row, q) {
  if (!q) return 0;
  let best = 0;
  const better = (score) => {
    if (score > best) best = score;
  };

  // 路 1：中文子串（query 含于 zh）
  if (row.zh && row.zh.includes(q)) {
    if (row.zh === q) better(SCORE.ZH_EXACT);
    else if (row.zh.startsWith(q)) better(SCORE.ZH_PREFIX);
    else better(SCORE.ZH_SUBSTRING);
  }

  // ASCII 查询才参与拼音/英文三路（CJK 字符不可能出现在拼音序列中）
  if (!CJK_RE.test(q)) {
    // 路 2：全拼
    if (row.fullPinyin) {
      if (row.fullPinyin === q) better(SCORE.PINYIN_EXACT);
      else if (row.fullPinyin.startsWith(q)) better(SCORE.PINYIN_PREFIX);
      else if (row.fullPinyin.includes(q)) better(SCORE.PINYIN_SUBSTRING);
    }
    // 路 3：首字母（前缀语义优先；两字符以上放行子串，覆盖
    // 「无括号矩阵」这类修饰语在前的中文名只输名词首字母的场景）
    if (row.initials) {
      if (row.initials === q) better(SCORE.INITIALS_EXACT);
      else if (row.initials.startsWith(q)) better(SCORE.INITIALS_PREFIX);
      else if (q.length >= 2 && row.initials.includes(q)) {
        better(SCORE.INITIALS_SUBSTRING);
      }
    }
    // 路 4：英文名前缀或子串
    if (row.name) {
      if (row.name === q) better(SCORE.ENGLISH_EXACT);
      else if (row.name.startsWith(q)) better(SCORE.ENGLISH_PREFIX);
      else if (row.name.includes(q)) better(SCORE.ENGLISH_SUBSTRING);
    }
  }

  return best;
}

/**
 * 核心搜索：四路打分 → 归一到同一刻度排序。
 * 排序稳定性契约：得分相同按条目原始顺序（order）升序，结果确定可复现。
 * @param {Array} indexRows 已填充拼音的索引
 * @param {string} rawQuery 原始用户输入
 * @param {{limit?: number}} [options]
 * @returns {Array} 命中的命令条目（得分降序）
 */
export function searchCommands(
  indexRows,
  rawQuery,
  { limit = DEFAULT_MATCH_LIMIT } = {},
) {
  const q = normalizeQuery(rawQuery);
  if (!q || !Array.isArray(indexRows)) return [];
  const hits = [];
  for (const row of indexRows) {
    const score = matchScore(row, q);
    if (score > 0) hits.push({ row, score });
  }
  hits.sort((a, b) => b.score - a.score || a.row.order - b.row.order);
  return hits.slice(0, Math.max(1, limit)).map((h) => h.row.entry);
}

/**
 * 空查询兜底列表：按数据库原始顺序返回条目（行内补全刚触发 `\`、
 * 面板初始列表共用），保证两入口默认视图一致。
 * @param {Array} indexRows 已填充拼音的索引
 * @param {{limit?: number}} [options]
 */
export function listCommands(indexRows, { limit = DEFAULT_MATCH_LIMIT } = {}) {
  if (!Array.isArray(indexRows)) return [];
  // buildCommandIndex 以保序 map 登记 order、fillPinyinSequences 就地填充
  // 不重排：索引本身已按 order 升序，直接截取前 limit 条即可
  return indexRows.slice(0, Math.max(1, limit)).map((r) => r.entry);
}

/* ── 懒加载共享索引（行内补全与命令面板共用同一实例与缓存） ──────────── */

let indexPromise = null;

/**
 * 获取共享匹配索引（含预计算拼音）。首次调用动态引入 pinyin-pro 构建；
 * 之后所有调用复用同一缓存，绝不每键重算。
 * @returns {Promise<Array>} resolve 为已填充拼音的索引行
 */
export function getCommandMatcherIndex() {
  if (!indexPromise) {
    indexPromise = import("pinyin-pro")
      .then(({ pinyin, customPinyin }) => {
        // customPinyin 注册的是模块级全局词典，必须在任何 pinyin() 调用
        // 前完成（本模块是 pinyin-pro 在应用内的唯一消费点）
        customPinyin(PINYIN_WORD_FIXES);
        return fillPinyinSequences(buildCommandIndex(), pinyin);
      })
      .catch((err) => {
        // 失败复位缓存允许重试；索引退化为纯中文/英文两路仍可用
        indexPromise = null;
        throw err;
      });
  }
  return indexPromise;
}

/**
 * 异步便捷入口：等待共享索引后执行 searchCommands。
 * @param {string} query 用户输入
 * @param {{limit?: number}} [options]
 */
export async function searchCommandsAsync(query, options) {
  const index = await getCommandMatcherIndex();
  return searchCommands(index, query, options);
}
