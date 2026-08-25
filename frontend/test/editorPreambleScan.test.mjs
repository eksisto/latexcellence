import test from "node:test";
import assert from "node:assert/strict";

import {
  buildPackageInsertion,
  extractPackages,
  findUsepackageInsertPoint,
  isPackageMissing,
  parseLoadedPackages,
  preambleText,
} from "../src/lib/editor/preambleScan.js";

const DOC = [
  "\\documentclass{article}",
  "% \\usepackage{commented}",
  "\\usepackage{graphicx}",
  "\\usepackage{amsmath, amssymb}", // 多包列表
  "",
  "\\begin{document}",
  "50\\% 数据 \\usepackage{inbody} 应被忽略",
  "\\end{document}",
].join("\n");

test("preambleText：截取 \\begin{document} 之前；无标记视为整篇", () => {
  const pre = preambleText(DOC);
  assert.ok(pre.startsWith("\\documentclass"));
  assert.ok(!pre.includes("\\begin{document}"));
  assert.equal(preambleText("\\usepackage{x}"), "\\usepackage{x}");
  assert.equal(preambleText(""), "");
});

test("parseLoadedPackages：多包列表 + 注释剥离（整行与行内注释均不计数）", () => {
  // parseLoadedPackages 只做文本层解析（含正文）；导言区过滤由 extractPackages 负责
  const loaded = parseLoadedPackages(preambleText(DOC));
  assert.ok(loaded.has("graphicx"));
  assert.ok(loaded.has("amsmath"));
  assert.ok(loaded.has("amssymb"));
  assert.ok(!loaded.has("commented"), "被注释的声明不应计数");
});

test("extractPackages：仅统计导言区；isPackageMissing 大小写不敏感", () => {
  const loaded = extractPackages(DOC);
  assert.ok(loaded.has("graphicx"));
  assert.equal(isPackageMissing(DOC, "AMSMATH"), false);
  assert.equal(isPackageMissing(DOC, "booktabs"), true);
  // pkg 为 null 恒不缺失
  assert.equal(isPackageMissing(DOC, null), false);
});

test("parseLoadedPackages：转义百分号不是注释起点", () => {
  const text = "\\documentclass{article}\n\\usepackage{a}\\% fake\n";
  assert.deepEqual([...parseLoadedPackages(text)], ["a"]);
});

test("插入点三级回退：最后一条 usepackage 之后 → documentclass 之后 → 顶部", () => {
  // 有 usepackage：插到最后一条声明行的下一行行首
  const p1 = findUsepackageInsertPoint(DOC);
  assert.equal(p1.anchor, "usepackage");
  const linesBefore = DOC.slice(0, p1.index).split("\n");
  assert.equal(
    linesBefore[linesBefore.length - 2],
    "\\usepackage{amsmath, amssymb}",
  );

  // 只有 documentclass
  const docClassOnly =
    "\\documentclass[12pt]{article}\n\\begin{document}\nx\n\\end{document}\n";
  const p2 = findUsepackageInsertPoint(docClassOnly);
  assert.equal(p2.anchor, "documentclass");
  assert.ok(docClassOnly.slice(0, p2.index).endsWith("{article}\n"));

  // 两者皆无（.sty 片段）
  const sty = "\\NeedsTeXFormat{LaTeX2e}\n\\ProvidesPackage{pkg}\n";
  const p3 = findUsepackageInsertPoint(sty);
  assert.deepEqual(p3, { index: 0, anchor: "top" });

  // 空文档
  assert.deepEqual(findUsepackageInsertPoint(""), { index: 0, anchor: "top" });
});

test("buildPackageInsertion：缺失时给出插入点与完整声明；已存在返回 null", () => {
  const insertion = buildPackageInsertion(DOC, "booktabs");
  assert.equal(insertion.text, "\\usepackage{booktabs}\n");
  assert.equal(insertion.index, DOC.indexOf("\n\\begin{document}"));
  assert.equal(buildPackageInsertion(DOC, "amsmath"), null);
  assert.equal(
    buildPackageInsertion("", "amsmath").text,
    "\\usepackage{amsmath}\n",
  );
});
