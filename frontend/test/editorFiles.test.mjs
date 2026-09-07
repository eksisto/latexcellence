import test from "node:test";
import assert from "node:assert/strict";

import {
  IMAGE_PREVIEW_SUFFIXES,
  joinProjectPath,
  validateNewFileName,
  MAX_EDITOR_FILE_CHARS,
  PREVIEWABLE_SUFFIXES,
  isEditableFilePath,
  isFileTooLarge,
  isImagePreviewFilePath,
  isPreviewableFilePath,
} from "../src/lib/editor/files.js";

test("可编辑白名单覆盖 .tex/.bib/.sty/.cls 且大小写不敏感", () => {
  assert.ok(isEditableFilePath("main.tex"));
  assert.ok(isEditableFilePath("refs.bib"));
  assert.ok(isEditableFilePath("chapters/intro.TEX"));
  // 大小写不敏感：大写后缀与白名单等价
  assert.ok(isEditableFilePath("styles/custom.STY"));
});

test("非白名单扩展名与非法输入被拒绝", () => {
  assert.equal(isEditableFilePath("figure.png"), false);
  assert.equal(isEditableFilePath("main.tex.bak"), false);
  assert.equal(isEditableFilePath(""), false);
  assert.equal(isEditableFilePath(null), false);
  assert.equal(isEditableFilePath(undefined), false);
  // .tex 是后缀而非子串
  assert.equal(isEditableFilePath("notes.tex.md"), false);
});

test("isFileTooLarge 边界：恰好上限放行，超一个字符拒绝", () => {
  const boundary = "a".repeat(MAX_EDITOR_FILE_CHARS);
  assert.equal(isFileTooLarge(boundary), false);
  assert.equal(isFileTooLarge(boundary + "a"), true);
  assert.equal(isFileTooLarge(MAX_EDITOR_FILE_CHARS), false);
  assert.equal(isFileTooLarge(MAX_EDITOR_FILE_CHARS + 1), true);
  // 空内容 / 非法输入不误判为超大
  assert.equal(isFileTooLarge(""), false);
  assert.equal(isFileTooLarge(null), false);
});

test("可预览白名单覆盖 PDF 与常见位图且大小写不敏感", () => {
  assert.ok(isPreviewableFilePath("main.pdf"));
  assert.ok(isPreviewableFilePath("figs/plot.PNG"));
  assert.ok(isPreviewableFilePath("photo.jpeg"));
  assert.ok(isPreviewableFilePath("anim.gif"));
  assert.ok(isPreviewableFilePath("modern.webp"));
  assert.ok(isPreviewableFilePath("scan.bmp"));
  // 白名单 = PDF + 位图子集
  assert.equal(PREVIEWABLE_SUFFIXES.length, IMAGE_PREVIEW_SUFFIXES.length + 1);
});

test("isImagePreviewFilePath 区分位图与 PDF", () => {
  assert.ok(isImagePreviewFilePath("a.png"));
  assert.ok(isImagePreviewFilePath("figs/b.JPG"));
  assert.equal(isImagePreviewFilePath("main.pdf"), false);
});

test("可预览白名单不误收其他扩展名与非法输入", () => {
  // .tex 是可编辑而非可预览；后缀而非子串；SVG/EPS 刻意不入白名单
  assert.equal(isPreviewableFilePath("main.tex"), false);
  assert.equal(isPreviewableFilePath("notes.pdf.md"), false);
  assert.equal(isPreviewableFilePath("vector.svg"), false);
  assert.equal(isPreviewableFilePath("legacy.eps"), false);
  assert.equal(isPreviewableFilePath(""), false);
  assert.equal(isPreviewableFilePath(null), false);
  assert.equal(isPreviewableFilePath(undefined), false);
});

test("PDF 同时不在可编辑白名单：预览分流不进文本缓冲区", () => {
  assert.equal(isEditableFilePath("main.pdf"), false);
  assert.equal(isPreviewableFilePath("main.pdf"), true);
});

test("validateNewFileName：合法单段白名单文件名放行", () => {
  assert.equal(validateNewFileName("new.tex"), null);
  assert.equal(validateNewFileName("  appendix.TEX  "), null);
  assert.equal(validateNewFileName("refs.bib"), null);
  assert.equal(validateNewFileName("custom.sty"), null);
  assert.equal(validateNewFileName("book.cls"), null);
});

test("validateNewFileName：空名/路径分隔符/白名单外后缀返回错误码", () => {
  assert.equal(validateNewFileName(""), "empty");
  assert.equal(validateNewFileName("   "), "empty");
  assert.equal(validateNewFileName(null), "empty");
  // 单段约束：文件名不允许夹带路径（目标目录由树节点决定）
  assert.equal(validateNewFileName("a/b.tex"), "separator");
  assert.equal(validateNewFileName("a\\b.tex"), "separator");
  assert.equal(validateNewFileName(".."), "separator");
  assert.equal(validateNewFileName("."), "separator");
  // 后缀白名单与 isEditableFilePath 同源
  assert.equal(validateNewFileName("notes.txt"), "suffix");
  assert.equal(validateNewFileName("noext"), "suffix");
});

test("joinProjectPath：根目录返回文件名，子目录拼相对路径", () => {
  assert.equal(joinProjectPath(null, "a.tex"), "a.tex");
  assert.equal(joinProjectPath("", "a.tex"), "a.tex");
  assert.equal(joinProjectPath("chapters", "intro.tex"), "chapters/intro.tex");
  // 容忍树节点路径尾部斜杠
  assert.equal(joinProjectPath("figs/", "plot.tex"), "figs/plot.tex");
});
