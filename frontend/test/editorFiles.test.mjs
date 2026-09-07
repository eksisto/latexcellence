import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import {
  IMAGE_PREVIEW_SUFFIXES,
  joinProjectPath,
  validateNewFileName,
  validateUploadFileName,
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

test("validateUploadFileName：任意后缀的正常文件名放行（决策 D1）", () => {
  assert.equal(validateUploadFileName("figure.png"), null);
  assert.equal(validateUploadFileName("plot.PNG"), null);
  assert.equal(validateUploadFileName("data.csv"), null);
  assert.equal(validateUploadFileName("archive.tar.gz"), null);
  assert.equal(validateUploadFileName("main.tex"), null);
  // 任意格式：无后缀也放行（服务端不限后缀）
  assert.equal(validateUploadFileName("Makefile"), null);
  assert.equal(validateUploadFileName("包含空格 与-横线_下划线.pdf"), null);
});

test("validateUploadFileName：空名返回 empty", () => {
  assert.equal(validateUploadFileName(""), "empty");
  assert.equal(validateUploadFileName("   "), "empty");
  assert.equal(validateUploadFileName(null), "empty");
  assert.equal(validateUploadFileName(undefined), "empty");
  assert.equal(validateUploadFileName(42), "empty");
});

test("validateUploadFileName：路径分隔符返回 separator", () => {
  // 单段约束：File.name 不允许夹带路径（目标目录由树节点决定）
  assert.equal(validateUploadFileName("a/b.png"), "separator");
  assert.equal(validateUploadFileName("a\\b.png"), "separator");
  assert.equal(validateUploadFileName("dir/nested/file.tex"), "separator");
});

test("validateUploadFileName：控制字符返回 control", () => {
  assert.equal(validateUploadFileName("a\u0000b.png"), "control");
  assert.equal(validateUploadFileName("a\nb.png"), "control");
  assert.equal(validateUploadFileName("a\tb.png"), "control");
  assert.equal(validateUploadFileName("a\u001Fb.png"), "control");
  assert.equal(validateUploadFileName("a\u007Fb.png"), "control");
});

test("validateUploadFileName：点开头隐藏名返回 hidden", () => {
  assert.equal(validateUploadFileName(".gitignore"), "hidden");
  assert.equal(validateUploadFileName(".."), "hidden");
  assert.equal(validateUploadFileName(".hidden.png"), "hidden");
});

test("joinProjectPath：上传资源到根目录与子目录（正斜杠，D8 口径）", () => {
  // 根目录上传为纯文件名
  assert.equal(joinProjectPath(null, "fig.png"), "fig.png");
  assert.equal(joinProjectPath("", "plot.pdf"), "plot.pdf");
  // 子目录上传拼相对路径（\includegraphics 引用口径：相对项目根、正斜杠）
  assert.equal(
    joinProjectPath("figures", "scheme1.png"),
    "figures/scheme1.png",
  );
  assert.equal(
    joinProjectPath("figures/scan", "p.1.jpg"),
    "figures/scan/p.1.jpg",
  );
});

test("上传消毒错误码与三语 i18n 条目一一对应（动态键门禁缺口回归）", () => {
  // validateUploadFileName 的错误码经模板字符串动态消费
  // （useEditorBuffer：t(`components:projectExplorer.uploadFileError.${code}`)），
  // i18nCoverage 只扫静态字面量键、i18nLocales 只查三语平价——三语齐缺时
  // 既有门禁全部放行，界面会直接渲染原始键名。此处逐语逐键钉死消费契约。
  const localesDir = join(
    dirname(fileURLToPath(import.meta.url)),
    "../src/i18n/locales",
  );
  const codes = ["empty", "separator", "control", "hidden"];
  for (const locale of ["zh", "zh-TW", "en"]) {
    const dict = JSON.parse(
      readFileSync(join(localesDir, `${locale}.json`), "utf8"),
    );
    const uploadFileError =
      dict.components?.projectExplorer?.uploadFileError ?? {};
    for (const code of codes) {
      assert.ok(
        typeof uploadFileError[code] === "string" &&
          uploadFileError[code].length > 0,
        `${locale}.components.projectExplorer.uploadFileError.${code} 缺失`,
      );
    }
    assert.ok(
      dict.errors?.uploadFileTooLarge,
      `${locale}.errors.uploadFileTooLarge 缺失`,
    );
  }
  // 后端 413 file_too_large → errors 命名空间的映射链路：EXACT_MAP 登记
  // 是 translateError 命中的前提（multiUserLocales 的固定 ERROR_CODES 清单
  // 不含编辑器文件操作错误码，此处钉死该映射不被静默删改）
  const errorMessagesSrc = readFileSync(
    join(
      dirname(fileURLToPath(import.meta.url)),
      "../src/utils/errorMessages.js",
    ),
    "utf8",
  );
  assert.match(errorMessagesSrc, /file_too_large:\s*"uploadFileTooLarge"/);
});
