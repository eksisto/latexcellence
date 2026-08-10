/* ============================================================
 * LateXcellence · Showcase — 主题 CSS 生成器
 *
 * css/themes.css 不再手工维护，而是从 frontend/src/theme/*.css
 * 按 THEME_ORDER 顺序拼接生成（与 useThemeManager.js 的顺序一致）。
 *
 * 用法：
 *   npm run themes:gen    # 重新生成 css/themes.css
 *   npm run themes:check  # 检查是否漂移（CI 可加这一条）
 * ============================================================ */

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const SHOWCASE = path.resolve(HERE, "..");
const THEME_DIR = path.resolve(SHOWCASE, "../frontend/src/theme");
const OUT = path.join(SHOWCASE, "css", "themes.css");

/* 顺序必须与 frontend/src/theme/useThemeManager.js 的 THEME_ORDER 保持一致 */
export const THEME_ORDER = [
  "yingtaohong",
  "hailuocheng",
  "hupohuang",
  "tingwulv",
  "jingtailan",
  "ningyezi",
  "youminghei",
];

/* 拼接生成 themes.css 的内容（LF 换行，与仓库 .gitattributes 的 eol=lf 一致） */
export function buildThemesCss() {
  const parts = THEME_ORDER.map((id) => {
    const file = path.join(THEME_DIR, `${id}.css`);
    if (!fs.existsSync(file)) {
      throw new Error(`主题源缺失: ${file}`);
    }
    return fs.readFileSync(file, "utf8").replace(/\r\n/g, "\n").trimEnd();
  });
  return parts.join("\n") + "\n";
}

function main() {
  const css = buildThemesCss();
  fs.writeFileSync(OUT, css, "utf8");
  console.log(
    `已生成 ${path.relative(SHOWCASE, OUT)}（${THEME_ORDER.length} 个主题）。`,
  );
}

if (
  process.argv[1] &&
  path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)
) {
  main();
}
