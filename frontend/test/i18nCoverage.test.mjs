import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

// i18n 消费覆盖门禁：src 下每个静态 t("...") / translate("...") 字面量键
// 必须能在语言文件（按文件 useTranslation 绑定的命名空间）中解析到条目。
// 防护的缺陷类：键在 zh / zh-TW / en 三份文件中同缺——三语互一致测试无法发现，
// 运行时表现为界面直接渲染原始键名。
//
// 已知限制（静态扫描的边界，不视为缺陷）：
// - 模板字符串 / 变量拼接的动态键不可见；
// - 命名空间绑定取自 useTranslation(...) 的字符串或数组字面量。

const FRONTEND_ROOT = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "..",
);
const SRC_DIR = path.join(FRONTEND_ROOT, "src");
const LOCALES = ["zh", "zh-TW", "en"];

const T_CALL_PATTERN = /(?:\bt|translate)\(\s*["'`]([A-Za-z0-9_.:-]+)["'`]/g;
const NS_BINDING_PATTERN =
  /useTranslation\(\s*(?:["']([^"']+)["']|\[([^\]]*)\])/g;
const ARRAY_NS_PATTERN = /["']([^"']+)["']/g;

function listSourceFiles(dir) {
  const out = [];
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) out.push(...listSourceFiles(full));
    else if (/\.[jt]sx?$/.test(entry.name)) out.push(full);
  }
  return out;
}

function walk(dict, dottedKey) {
  let cur = dict;
  for (const part of dottedKey.split(".")) {
    if (cur && typeof cur === "object" && part in cur) cur = cur[part];
    else return undefined;
  }
  return cur;
}

function loadLocales() {
  const map = {};
  for (const loc of LOCALES) {
    map[loc] = JSON.parse(
      readFileSync(
        path.join(FRONTEND_ROOT, "src", "i18n", "locales", `${loc}.json`),
        "utf8",
      ),
    );
  }
  return map;
}

function collectBoundNamespaces(text) {
  const bound = [];
  for (const m of text.matchAll(NS_BINDING_PATTERN)) {
    if (m[1]) {
      bound.push(m[1]);
    } else if (m[2]) {
      for (const arr of m[2].matchAll(ARRAY_NS_PATTERN)) bound.push(arr[1]);
    }
  }
  return [...new Set(bound)];
}

test("所有静态 t() 消费键在三语文件中均可解析", () => {
  const locales = loadLocales();
  const missing = new Map(); // key -> [file...]

  for (const file of listSourceFiles(SRC_DIR)) {
    const text = readFileSync(file, "utf8");
    const boundNamespaces = collectBoundNamespaces(text);
    for (const m of text.matchAll(T_CALL_PATTERN)) {
      const key = m[1];
      // 显式前缀键只切第一个冒号（ns:key，key 内可含点）；裸键走文件绑定的命名空间
      const colonIndex = key.indexOf(":");
      const candidates =
        colonIndex !== -1
          ? [[key.slice(0, colonIndex), key.slice(colonIndex + 1)]]
          : (boundNamespaces.length ? boundNamespaces : ["common"]).map(
              (ns) => [ns, key],
            );
      const resolved = candidates.some(([ns, rest]) =>
        LOCALES.some((loc) => {
          const nsDict = locales[loc]?.[ns];
          return nsDict !== undefined && walk(nsDict, rest) !== undefined;
        }),
      );
      if (!resolved) {
        const rel = path.relative(SRC_DIR, file);
        if (!missing.has(key)) missing.set(key, []);
        missing.get(key).push(rel);
      }
    }
  }

  const detail = [...missing.entries()]
    .map(([key, files]) => `  ${key}  <- ${[...new Set(files)].join(", ")}`)
    .join("\n");
  assert.equal(
    missing.size,
    0,
    `以下 i18n 键在语言文件中缺失（三语均未命中）：\n${detail}`,
  );
});
