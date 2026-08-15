export const LANGUAGE_NAMES = Object.freeze({
  zh: "Chinese",
  "zh-TW": "Traditional Chinese",
  en: "English",
});

export const SUPPORTED_LANGUAGE_CODES = Object.keys(LANGUAGE_NAMES);

/**
 * 将浏览器可能返回的完整语言标签归一为前端支持的语言代码：
 * en-US / en-GB → en；zh-CN / zh-Hans → zh；zh-TW / zh-HK / zh-Hant → zh-TW。
 * 未支持的语言回退到默认简体中文（与 fallbackLng 一致）。
 */
export function normalizeLanguageCode(lng) {
  const normalized = String(lng || "")
    .toLowerCase()
    .replaceAll("_", "-");
  const [lang, region] = normalized.split("-");

  if (lang === "en") return "en";
  if (lang === "zh") {
    if (
      region === "tw" ||
      region === "hk" ||
      region === "mo" ||
      region === "hant"
    ) {
      return "zh-TW";
    }
    return "zh";
  }
  return "zh";
}

export function getUiLanguageName(code) {
  return LANGUAGE_NAMES[code] || code;
}
