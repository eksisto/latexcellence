import test from "node:test";
import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const localesDir = join(
  dirname(fileURLToPath(import.meta.url)),
  "../src/i18n/locales",
);

/** 递归收集 JSON 中所有叶子翻译键（如 common.applyAllConfirm）。 */
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

/**
 * 插值变量与组件传参一致（回归 editor v3 阶段二 customCount 缺陷）：
 * t(key, options) 的变量名与三语条目的 {{占位符}} 不一致时，i18next 不会
 * 报错，而是把原始占位符（如 "{{current}}"）直接渲染进界面；键存在性
 * 门禁（上方用例与 i18nCoverage）均无法发现此类缺陷。此处对含参数化
 * 插值的条目做静态交叉核对：组件实参名集合 === 三语占位符名集合。
 */
test("参数化插值条目的组件传参变量与三语占位符一致", () => {
  const srcRoot = join(localesDir, "..", ".."); // frontend/src
  const readSrc = (rel) => readFileSync(join(srcRoot, rel), "utf8");
  // 抽取 t("ns:key", { a, b }) 选项对象中的变量名（选项对象内无嵌套花括号）
  const tCallSites = [
    {
      file: "components/editor/EditorSettingsDialog.jsx",
      // 语言文件中的完整点路径（含命名空间顶层键）
      dictPath: "components.editorSettings.customCount",
      // 供正则匹配 t() 调用的 key 片段（ns: 前缀之后的部分）
      key: "editorSettings.customCount",
    },
  ];
  const placeholderNames = (template) =>
    [...template.matchAll(/\{\{(\w+)\}\}/g)].map((m) => m[1]).sort();

  for (const { file, key, dictPath } of tCallSites) {
    const m = new RegExp(
      `${key.replace(/\./g, "\\.")}",\\s*\\{([^}]+)\\}`,
    ).exec(readSrc(file));
    assert.ok(m, `${file} 中应存在 ${key} 的 t() 调用（带选项对象）`);
    const passed = [...m[1].matchAll(/([A-Za-z_]\w*)\s*:/g)]
      .map((x) => x[1])
      .sort();

    for (const loc of localeFiles) {
      const data = JSON.parse(readFileSync(join(localesDir, loc), "utf8"));
      let cur = data;
      for (const part of dictPath.split(".")) cur = cur?.[part];
      assert.equal(typeof cur, "string", `${loc}.${dictPath} 应为字符串条目`);
      assert.deepEqual(
        placeholderNames(cur),
        passed,
        `${loc}.${dictPath} 的 {{占位符}} 与 ${file} 传参变量不一致（会渲染原始占位符）`,
      );
    }
  }
});
