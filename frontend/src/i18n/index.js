import i18n from "i18next";
import { initReactI18next } from "react-i18next";
import LanguageDetector from "i18next-browser-languagedetector";

import zh from "./locales/zh.json";
import en from "./locales/en.json";

const LANGUAGE_NAMES = {
  zh: "Chinese",
  en: "English",
};

export function getUiLanguage() {
  return LANGUAGE_NAMES[i18n.language] || i18n.language;
}

i18n
  .use(LanguageDetector)
  .use(initReactI18next)
  .init({
    resources: {
      zh: zh,
      en: en,
    },
    fallbackLng: "zh",
    ns: ["common", "pages", "components", "hooks", "errors"],
    defaultNS: "common",
    interpolation: {
      escapeValue: false,
    },
    detection: {
      order: ["localStorage", "navigator"],
      caches: ["localStorage"],
    },
  });

export default i18n;
