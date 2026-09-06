/**
 * Lezer grammar 生成脚本（editor v3 阶段六a / 决策 Q14）。
 *
 * 将同目录 latex.grammar 编译为 parser.js + parser.terms.js（生成物不入库，
 * 已列入 .gitignore）。经 npm scripts 的 predev / prebuild / pretest 钩子在
 * dev / build / test 前自动执行，保证 clean checkout 后直接构建可重复。
 *
 * 使用 @lezer/generator（MIT, devDependency；lezer-generator 改名后的现行
 * 包）的 Node API，不用 CLI，跨 Windows / POSIX shell 一致。
 */

import { buildParserFile } from "@lezer/generator";
import { readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const grammarPath = join(here, "latex.grammar");

let result;
try {
  result = buildParserFile(readFileSync(grammarPath, "utf8"), {
    fileName: grammarPath,
  });
} catch (error) {
  console.error(`[latexGrammar] grammar 编译失败: ${error.message}`);
  process.exit(1);
}

writeFileSync(join(here, "parser.js"), result.parser);
writeFileSync(join(here, "parser.terms.js"), result.terms);
console.log(
  `[latexGrammar] parser 生成完成（parser.js ${result.parser.length} B / parser.terms.js ${result.terms.length} B）`,
);
