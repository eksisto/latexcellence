import test from "node:test";
import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const localesDir = join(
  dirname(fileURLToPath(import.meta.url)),
  "../src/i18n/locales",
);

/** 递归收集 JSON 中所有叶子翻译键（如 common.applyAllConfirmTitle）。 */
function collectKeys(obj, prefix = "") {
  const keys = new Set();
  for (const [key, value] of Object.entries(obj)) {
    const path = prefix ? `${prefix}.${key}` : key;
    if (value && typeof value === "object" && !Array.isArray(value)) {
      for (const k of collectKeys(value, path)) {
        keys.add(k);
      }
    } else {
      keys.add(path);
    }
  }
  return keys;
}

const localeFiles = readdirSync(localesDir)
  .filter((file) => file.endsWith(".json"))
  .sort();

test("所有语言文件的翻译键与 en.json 保持一致", () => {
  const reference = JSON.parse(
    readFileSync(join(localesDir, "en.json"), "utf8"),
  );
  const refKeys = collectKeys(reference);

  for (const file of localeFiles) {
    const data = JSON.parse(readFileSync(join(localesDir, file), "utf8"));
    const keys = collectKeys(data);
    const missing = [...refKeys].filter((key) => !keys.has(key)).sort();
    const extra = [...keys].filter((key) => !refKeys.has(key)).sort();

    assert.deepEqual(
      { missing, extra },
      { missing: [], extra: [] },
      `${file} 的翻译键与 en.json 不一致`,
    );
  }
});
