import test from "node:test";
import assert from "node:assert/strict";

import {
  EDITABLE_SUFFIXES,
  IMAGE_PREVIEW_SUFFIXES,
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
  assert.ok(isEditableFilePath("styles/custom.Style".replace("Style", "STY")));
  assert.ok(EDITABLE_SUFFIXES.length === 4);
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
