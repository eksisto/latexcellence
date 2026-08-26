/**
 * 命令中文名的本地化展示（行内补全 detail / 命令面板中名列 /
 * 宏包缺失提示的命令名 共用）。
 *
 * 数据库 zh 字段恒为简体中文——它同时是中文子串与拼音检索的数据源
 * （matcher.js 四路匹配），因此只在展示层做本地化，不动检索数据：
 *   - zh（简体界面）→ 原样展示；
 *   - zh-TW（繁体界面）→ 经 OpenCC 简→繁（台湾正体）转换展示，
 *     opencc-js 与 pinyin-pro 同款动态 import() 按需 chunk，不进主包，
 *     转换资源加载失败时回退简体原文；
 *   - en 等非中文界面 → null，由调用方隐藏中文名（英文命令名已自说明）。
 */

import { normalizeLanguageCode } from "../../i18n/language.js";

/** 展示意图：simplified 简体原样 | traditional 简→繁转换 | hidden 隐藏 */
export function commandZhIntent(language) {
  const lng = normalizeLanguageCode(language);
  if (lng === "zh-TW") return "traditional";
  if (lng === "zh") return "simplified";
  return "hidden";
}

const identity = (text) => text;

/* ── 懒加载共享转换器（模块级 promise 缓存，失败复位允许重试） ─────────── */

let converterPromise = null;

function loadCnToTwConverter() {
  if (!converterPromise) {
    converterPromise = import("opencc-js")
      .then((mod) => {
        const OpenCC = mod?.default ?? mod;
        return OpenCC.Converter({ from: "cn", to: "tw" });
      })
      .catch((err) => {
        // 失败复位缓存允许下次重试；本次调用由上层回退简体
        converterPromise = null;
        throw err;
      });
  }
  return converterPromise;
}

/**
 * 取当前界面语言下的中文名展示函数。
 * @param {string} language i18next 当前语言码（i18n.language）
 * @returns {Promise<((zh: string) => string) | null>}
 *   null = 该语言下应隐藏中文名；否则返回原样或简转繁映射函数
 */
export async function getCommandZhDisplay(language) {
  const intent = commandZhIntent(language);
  if (intent === "hidden") return null;
  if (intent === "simplified") return identity;
  try {
    return await loadCnToTwConverter();
  } catch {
    // 转换 chunk 加载失败：退回简体原文，保证功能可用
    return identity;
  }
}
