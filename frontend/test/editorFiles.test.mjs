import test from "node:test";
import assert from "node:assert/strict";

import {
  EDITABLE_SUFFIXES,
  MAX_EDITOR_FILE_CHARS,
  isEditableFilePath,
  isFileTooLarge,
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
