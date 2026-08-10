/* ============================================================
 * LateXcellence · Showcase — 主题漂移检查
 * 校验 css/themes.css 与 frontend/src/theme/*.css 是否一致。
 * 漂移时退出码为 1（可在 CI / pre-commit 中使用）。
 * ============================================================ */

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { buildThemesCss } from "./gen-themes.mjs";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const SHOWCASE = path.resolve(HERE, "..");
const OUT = path.join(SHOWCASE, "css", "themes.css");

const current = fs.existsSync(OUT)
  ? fs.readFileSync(OUT, "utf8").replace(/\r\n/g, "\n")
  : "";
const expected = buildThemesCss();

if (current === expected) {
  console.log("✓ css/themes.css 与 frontend/src/theme/*.css 一致，无漂移。");
} else {
  console.error(
    "✗ css/themes.css 已与 frontend 主题源漂移。请运行 `npm run themes:gen` 重新生成后提交。",
  );
  process.exit(1);
}
