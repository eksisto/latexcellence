import { test } from "node:test";
import assert from "node:assert/strict";
import {
  escapeHtml,
  highlightHtml,
  highlightToHtmlLines,
  languageFromFilename,
  normalizeLanguage,
} from "./highlight.js";

test("normalizeLanguage 已注册语言原样返回", () => {
  assert.equal(normalizeLanguage("latex"), "latex");
  assert.equal(normalizeLanguage("tex"), "tex");
  assert.equal(normalizeLanguage("bibtex"), "bibtex");
  assert.equal(normalizeLanguage("markdown"), "markdown");
  assert.equal(normalizeLanguage("python"), "python");
  assert.equal(normalizeLanguage("json"), "json");
  assert.equal(normalizeLanguage("xml"), "xml");
  assert.equal(normalizeLanguage("html"), "html");
  assert.equal(normalizeLanguage("diff"), "diff");
  assert.equal(normalizeLanguage("plaintext"), "plaintext");
});

test("normalizeLanguage 大小写与首尾空白不敏感", () => {
  assert.equal(normalizeLanguage(" LaTeX "), "latex");
  assert.equal(normalizeLanguage("PYTHON"), "python");
});

test("normalizeLanguage 未注册/空值回退 plaintext", () => {
  assert.equal(normalizeLanguage("js"), "plaintext");
  assert.equal(normalizeLanguage("c++"), "plaintext");
  assert.equal(normalizeLanguage(""), "plaintext");
  assert.equal(normalizeLanguage(undefined), "plaintext");
  assert.equal(normalizeLanguage(null), "plaintext");
});

test("languageFromFilename 按扩展名推断语言", () => {
  assert.equal(languageFromFilename("main.tex"), "latex");
  assert.equal(languageFromFilename("preamble.sty"), "latex");
  assert.equal(languageFromFilename("book.cls"), "latex");
  assert.equal(languageFromFilename("chapter.ltx"), "latex");
  assert.equal(languageFromFilename("references.bib"), "bibtex");
  assert.equal(languageFromFilename("README.md"), "markdown");
  assert.equal(languageFromFilename("README.markdown"), "markdown");
  assert.equal(languageFromFilename("script.py"), "python");
  assert.equal(languageFromFilename("data.json"), "json");
  assert.equal(languageFromFilename("page.html"), "html");
  assert.equal(languageFromFilename("page.htm"), "html");
  assert.equal(languageFromFilename("main.xml"), "xml");
  assert.equal(languageFromFilename("notes.txt"), "plaintext");
  assert.equal(languageFromFilename("MAIN.TEX"), "latex");
  assert.equal(languageFromFilename("noext"), "plaintext");
  assert.equal(languageFromFilename(""), "plaintext");
});

test("escapeHtml 转义 & < >", () => {
  assert.equal(escapeHtml("<a>&"), "&lt;a&gt;&amp;");
  assert.equal(escapeHtml("plain"), "plain");
});

test("highlightHtml 转义危险字符，可安全注入", () => {
  const html = highlightHtml("<script>alert(1)</script>", "latex");
  assert.ok(!html.includes("<script>"));
  assert.ok(html.includes("&lt;script&gt;"));
});

test("highlightHtml 输出 latex 关键字 span", () => {
  const html = highlightHtml("\\begin{document}", "latex");
  assert.ok(html.includes('<span class="hljs-keyword">'));
  assert.ok(html.includes("\\begin"));
});

test("highlightHtml 未注册语言按 plaintext 处理且仍转义", () => {
  assert.equal(highlightHtml("<b>&</b>", "js"), "&lt;b&gt;&amp;&lt;/b&gt;");
});

test("highlightHtml 空字符串/空值返回空", () => {
  assert.equal(highlightHtml("", "latex"), "");
  assert.equal(highlightHtml(undefined), "");
});

test("highlightToHtmlLines 多行纯文本按行拆分", () => {
  assert.deepEqual(highlightToHtmlLines("a\nb\nc", "plaintext"), [
    "a",
    "b",
    "c",
  ]);
});

test("highlightToHtmlLines 跨行 span 每行自闭合且内容仍转义", () => {
  const lines = highlightToHtmlLines('s = """a\n<b>&"""', "python");
  assert.equal(lines.length, 2);
  for (const line of lines) {
    const opens = (line.match(/<span\b/g) || []).length;
    const closes = (line.match(/<\/span>/g) || []).length;
    assert.equal(opens, closes);
  }
  const joined = lines.join("\n");
  assert.ok(!joined.includes("<b>"));
  assert.ok(joined.includes("&lt;b&gt;"));
  assert.ok(joined.includes("&amp;"));
});

test("highlightToHtmlLines 末尾换行不产生空行", () => {
  assert.deepEqual(highlightToHtmlLines("a\nb\n", "plaintext"), ["a", "b"]);
});

test("highlightToHtmlLines CRLF 归一化", () => {
  assert.deepEqual(highlightToHtmlLines("a\r\nb", "plaintext"), ["a", "b"]);
});

test("highlightToHtmlLines 空字符串返回空数组", () => {
  assert.deepEqual(highlightToHtmlLines("", "latex"), []);
});

test("bibtex 本地语法：关键字、字段与字符串高亮", () => {
  const html = highlightHtml(
    '@article{key,\n  title = "A & B",\n  author = {X}}',
    "bibtex",
  );
  assert.ok(html.includes('<span class="hljs-keyword">@article</span>'));
  assert.ok(html.includes('<span class="hljs-params">title</span>'));
  assert.ok(html.includes('<span class="hljs-string">'));
  assert.ok(html.includes("A &amp; B"));
});
