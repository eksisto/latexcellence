import i18n from "i18next";
import { initReactI18next } from "react-i18next";
import LanguageDetector from "i18next-browser-languagedetector";

import zh from "./locales/zh.json";
import {
  SUPPORTED_LANGUAGE_CODES,
  getUiLanguageName,
  normalizeLanguageCode,
} from "./language";

// bundle-dynamic-imports：语言包体量大（单份 ~110KB JSON）且任一会话只用到
// 一种，默认语言 zh 静态打入首屏保证可用性，zh-TW / en 经自定义 backend
// 在首次使用时动态加载注入，避免 2/3 的语言包体积白白进入 entry chunk。
const lazyLocaleImporters = {
  "zh-TW": () => import("./locales/zh-TW.json"),
  en: () => import("./locales/en.json"),
};

// 语言 → JSON 模块 Promise 缓存：同一语言的全部 namespace 共享一次 import()
const localeModulePromises = new Map();

function importLocale(lng) {
  if (!localeModulePromises.has(lng)) {
    localeModulePromises.set(lng, lazyLocaleImporters[lng]());
  }
  return localeModulePromises.get(lng);
}

const lazyLocaleBackend = {
  type: "backend",
  init() {},
  read(lng, ns, callback) {
    const loader = lazyLocaleImporters[lng];
    if (!loader) return callback(null, null);
    importLocale(lng)
      .then((mod) => callback(null, mod.default?.[ns] ?? null))
      .catch((err) => callback(err, null));
  },
};

export function getUiLanguage() {
  return getUiLanguageName(i18n.language);
}

/**
 * i18n 初始化完成（含初始语言的动态语言包加载）后 resolve；
 * main.jsx 等待该 Promise 再渲染，避免非 zh 用户首帧闪现 fallback 文案。
 * 无论初始化是否报错都 resolve，保证应用可降级渲染。
 */
export const i18nReady = new Promise((resolve) => {
  i18n
    .use(LanguageDetector)
    .use(lazyLocaleBackend)
    .use(initReactI18next)
    .init(
      {
        // partialBundledLanguages：resources 只含 zh（部分打包），
        // 其余语言由 lazyLocaleBackend 按需补齐
        resources: { zh },
        partialBundledLanguages: true,
        fallbackLng: "zh",
        supportedLngs: SUPPORTED_LANGUAGE_CODES,
        ns: ["common", "pages", "components", "hooks", "errors"],
        defaultNS: "common",
        interpolation: {
          escapeValue: false,
        },
        detection: {
          order: ["localStorage", "navigator"],
          caches: ["localStorage"],
          convertDetectedLanguage: normalizeLanguageCode,
        },
      },
      () => resolve(),
    );
});

export default i18n;
