import i18n from "i18next";
import { initReactI18next } from "react-i18next";
import LanguageDetector from "i18next-browser-languagedetector";

import zh from "./locales/zh.json";
import zhTW from "./locales/zh-TW.json";
import en from "./locales/en.json";
import {
  SUPPORTED_LANGUAGE_CODES,
  getUiLanguageName,
  normalizeLanguageCode,
} from "./language";

export function getUiLanguage() {
  return getUiLanguageName(i18n.language);
}

i18n
  .use(LanguageDetector)
  .use(initReactI18next)
  .init({
    resources: {
      zh: zh,
      "zh-TW": zhTW,
      en: en,
    },
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
  });

export default i18n;
