import { test } from "node:test";
import assert from "node:assert/strict";

import {
  SUPPORTED_LANGUAGE_CODES,
  getUiLanguageName,
  normalizeLanguageCode,
} from "./language.js";

test("normalizeLanguageCode 将英文地区标签归一为 en", () => {
  assert.equal(normalizeLanguageCode("en-US"), "en");
  assert.equal(normalizeLanguageCode("en-GB"), "en");
  assert.equal(normalizeLanguageCode("EN_us"), "en");
  assert.equal(normalizeLanguageCode("en"), "en");
});

test("normalizeLanguageCode 将简体中文标签归一为 zh", () => {
  assert.equal(normalizeLanguageCode("zh-CN"), "zh");
  assert.equal(normalizeLanguageCode("zh-SG"), "zh");
  assert.equal(normalizeLanguageCode("zh-Hans"), "zh");
  assert.equal(normalizeLanguageCode("zh"), "zh");
});

test("normalizeLanguageCode 将繁体中文标签归一为 zh-TW", () => {
  assert.equal(normalizeLanguageCode("zh-TW"), "zh-TW");
  assert.equal(normalizeLanguageCode("zh-HK"), "zh-TW");
  assert.equal(normalizeLanguageCode("zh-MO"), "zh-TW");
  assert.equal(normalizeLanguageCode("zh-Hant"), "zh-TW");
});

test("normalizeLanguageCode 未支持语言与空值回退 zh", () => {
  assert.equal(normalizeLanguageCode("fr-FR"), "zh");
  assert.equal(normalizeLanguageCode(""), "zh");
  assert.equal(normalizeLanguageCode(undefined), "zh");
  assert.equal(normalizeLanguageCode(null), "zh");
});

test("getUiLanguageName 返回后端可识别的完整语言名", () => {
  assert.equal(getUiLanguageName("zh"), "Chinese");
  assert.equal(getUiLanguageName("zh-TW"), "Traditional Chinese");
  assert.equal(getUiLanguageName("en"), "English");
});

test("支持的语言代码包含简中、繁中、英文", () => {
  assert.deepEqual(SUPPORTED_LANGUAGE_CODES, ["zh", "zh-TW", "en"]);
});
