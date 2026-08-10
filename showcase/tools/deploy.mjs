/* ============================================================
 * LateXcellence · Showcase — 部署构建工具
 *
 * 把网站资源（index.html / css/ / js/ / assets/）拷贝到 public/，
 * 并对 HTML / CSS / JS 做无损压缩，产出可直接部署到任意静态服务器的目录。
 * README 等文档、server.js、tools/ 等开发文件不会进入 public/。
 *
 * 用法：
 *   npm run deploy        # 生成 public/
 *
 * 说明：
 *   - 零依赖：只用 Node.js 内置模块（fs / os / path / zlib / child_process）。
 *   - 每次运行会先清空 public/ 再重新生成，避免残留旧文件。
 *   - JS 压缩后会用 `node --check` 做一次语法自检，损坏时构建失败并退出码 1。
 * ============================================================ */

import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import zlib from "node:zlib";
import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const SHOWCASE = path.resolve(HERE, "..");
const PUBLIC = path.join(SHOWCASE, "public");

/* 需要发布的网站资源（入口为文件或目录；其余文件一律不进入 public/） */
const SOURCES = ["index.html", "css", "js", "assets"];

/* 需要压缩的扩展名；其余（如 .svg）原样拷贝 */
const MINIFY_EXT = new Set([".html", ".css", ".js"]);

/* ------------------------------------------------------------------ */
/* JS 压缩：词法级，去掉注释、折叠空白，只在必要处保留单个空格。        */
/* 原则：绝不新增源中没有的空格；两个 token 的边界可能被误合并时，      */
/* 若源中二者之间有空白则保留一个空格。                                */
/* ------------------------------------------------------------------ */

/* 运算符字符：两个这样的字符相邻可能拼成更长运算符（++ / // / && / => 等），
 * 因此源中二者之间有空白时不能删除。 */
const OP_CHARS = new Set("=+-*/%&|^!<>?:.~");

/* 这些关键字之后允许出现正则字面量（如 return /re/、typeof /x/） */
const REGEX_KEYWORDS = new Set([
  "return",
  "typeof",
  "instanceof",
  "in",
  "of",
  "new",
  "delete",
  "void",
  "throw",
  "case",
  "do",
  "else",
  "yield",
  "await",
]);

const WORD_START = /[A-Za-z_$]/;
const WORD_CHAR = /[A-Za-z0-9_$]/;
const DEC_DIGIT = /[0-9]/;

/* 多字符运算符 / 标点（按长度从长到短匹配） */
const PUNCTS = [
  ">>>=",
  "===",
  "!==",
  "**=",
  "<<=",
  ">>=",
  ">>>",
  "=>",
  "==",
  "!=",
  "<=",
  ">=",
  "&&",
  "||",
  "??",
  "?.",
  "++",
  "--",
  "+=",
  "-=",
  "*=",
  "/=",
  "%=",
  "&=",
  "|=",
  "^=",
  "<<",
  ">>",
  "**",
  "...",
];

/* 判断两个相邻 token 之间是否需要保留一个空格（防止被错误合并成新 token） */
function needsSpace(a, b) {
  if (!a || !b) return false;
  const aLast = a[a.length - 1];
  const bFirst = b[0];
  /* 两个词字符相邻会拼成标识符 / 数字（return x、case 1、typeof y） */
  if (WORD_CHAR.test(aLast) && WORD_CHAR.test(bFirst)) return true;
  /* 数字后紧跟 . 会拼成小数（5 .toString() 不能压成 5.toString()） */
  if (DEC_DIGIT.test(aLast) && bFirst === ".") return true;
  /* . 后紧跟数字会拼成小数（a .5 保持 a .5） */
  if (aLast === "." && DEC_DIGIT.test(bFirst)) return true;
  /* 两个运算符字符相邻可能拼成更长的运算符（+ + → ++、/ / → // 等） */
  if (OP_CHARS.has(aLast) && OP_CHARS.has(bFirst)) return true;
  return false;
}

/* 根据上一个 token 判断当前位置的 `/` 是正则字面量还是除法 */
function canStartRegex(prevType, prevText) {
  if (prevType === "word") return REGEX_KEYWORDS.has(prevText);
  if (
    prevType === "num" ||
    prevType === "str" ||
    prevType === "tmpl" ||
    prevType === "regex"
  ) {
    return false;
  }
  /* 标点：右括号 / 右方括号 / 自增自减 之后是除法；其余（含 } 与左括号等）允许正则 */
  return (
    prevText !== ")" &&
    prevText !== "]" &&
    prevText !== "++" &&
    prevText !== "--"
  );
}

function readString(src, i, quote) {
  let j = i + 1;
  while (j < src.length) {
    const c = src[j];
    if (c === "\\") {
      j += 2;
      continue;
    }
    if (c === quote) return { text: src.slice(i, j + 1), end: j + 1 };
    j += 1;
  }
  throw new Error("未闭合的字符串字面量");
}

function readRegex(src, i) {
  let j = i + 1;
  let inClass = false;
  while (j < src.length) {
    const c = src[j];
    if (c === "\\") {
      j += 2;
      continue;
    }
    if (c === "[") inClass = true;
    else if (c === "]") inClass = false;
    else if (c === "/" && !inClass) {
      j += 1;
      while (j < src.length && /[A-Za-z]/.test(src[j])) j += 1;
      return { text: src.slice(i, j), end: j };
    }
    j += 1;
  }
  throw new Error("未闭合的正则字面量");
}

function readNumber(src, i) {
  let j = i;
  if (src[j] === "0" && /[xXoObB]/.test(src[j + 1] || "")) {
    j += 2;
    while (j < src.length && /[0-9a-fA-F_]/.test(src[j])) j += 1;
    return j;
  }
  while (j < src.length && /[0-9_]/.test(src[j])) j += 1;
  /* 小数部分：. 后必须是数字或 e/E 指数，避免把 5.toString 的 . 吞进数字 */
  if (src[j] === ".") {
    const after = src[j + 1] || "";
    if (DEC_DIGIT.test(after) || /[eE]/.test(after)) {
      j += 1;
      while (j < src.length && /[0-9_]/.test(src[j])) j += 1;
    }
  }
  /* 指数部分 */
  if (/[eE]/.test(src[j] || "")) {
    let k = j + 1;
    if (/[+-]/.test(src[k] || "")) k += 1;
    if (DEC_DIGIT.test(src[k] || "")) {
      j = k;
      while (j < src.length && /[0-9_]/.test(src[j])) j += 1;
    }
  }
  return j;
}

function readPunctuator(src, i) {
  const rest = src.slice(i, i + 4);
  for (const p of PUNCTS) {
    if (rest.startsWith(p)) {
      /* a ?.5 非法：?. 后不能是数字，此时按 ? 与 .5 分别处理 */
      if (p === "?." && DEC_DIGIT.test(src[i + 2] || "")) continue;
      return { text: p, end: i + p.length };
    }
  }
  return { text: src[i], end: i + 1 };
}

/* 扫描模板字符串：原样保留文本，对 ${...} 内部表达式递归压缩 */
function scanTemplate(src, i) {
  const parts = ["`"];
  let j = i + 1;
  while (j < src.length) {
    const c = src[j];
    if (c === "\\") {
      parts.push(src.slice(j, j + 2));
      j += 2;
      continue;
    }
    if (c === "`") {
      parts.push("`");
      return { text: parts.join(""), end: j + 1 };
    }
    if (c === "$" && src[j + 1] === "{") {
      parts.push("${");
      const inner = scanJs(src, j + 2, true);
      parts.push(inner.text);
      parts.push("}");
      j = inner.end;
      continue;
    }
    parts.push(c);
    j += 1;
  }
  throw new Error("未闭合的模板字符串");
}

/* 扫描并压缩一段 JS（从 start 到 src 末尾；stopAtBrace 时在 ${...} 的 } 处停止） */
function scanJs(src, start, stopAtBrace) {
  const out = [];
  let prev = ""; // 上一个 token 文本
  let prevType = ""; // word / num / str / tmpl / regex / punct
  let prevEnd = start; // 上一个 token 在源码中的结束下标
  let braceDepth = 0;
  let i = start;

  const emit = (text, tokStart, tokEnd, type) => {
    if (tokStart > prevEnd && needsSpace(prev, text)) out.push(" ");
    out.push(text);
    prev = text;
    prevType = type;
    prevEnd = tokEnd;
  };

  while (i < src.length) {
    const c = src[i];

    /* 空白：跳过（是否需要补空格由 emit 依据“源中是否有间隔”决定） */
    if (/\s/.test(c)) {
      i += 1;
      continue;
    }

    /* 注释 */
    if (c === "/" && src[i + 1] === "/") {
      while (i < src.length && src[i] !== "\n") i += 1;
      continue;
    }
    if (c === "/" && src[i + 1] === "*") {
      i += 2;
      while (i < src.length && !(src[i] === "*" && src[i + 1] === "/")) i += 1;
      i += 2;
      continue;
    }

    const tokStart = i;

    /* 字符串 */
    if (c === '"' || c === "'") {
      const s = readString(src, i, c);
      emit(s.text, tokStart, s.end, "str");
      i = s.end;
      continue;
    }

    /* 模板字符串 */
    if (c === "`") {
      const t = scanTemplate(src, i);
      emit(t.text, tokStart, t.end, "tmpl");
      i = t.end;
      continue;
    }

    /* 正则字面量 */
    if (c === "/" && canStartRegex(prevType, prev)) {
      const r = readRegex(src, i);
      emit(r.text, tokStart, r.end, "regex");
      i = r.end;
      continue;
    }

    /* 数字 */
    if (DEC_DIGIT.test(c) || (c === "." && DEC_DIGIT.test(src[i + 1] || ""))) {
      const numEnd = readNumber(src, i);
      emit(src.slice(tokStart, numEnd), tokStart, numEnd, "num");
      i = numEnd;
      continue;
    }

    /* 标识符 / 关键字 */
    if (WORD_START.test(c)) {
      let j = i + 1;
      while (j < src.length && WORD_CHAR.test(src[j])) j += 1;
      emit(src.slice(tokStart, j), tokStart, j, "word");
      i = j;
      continue;
    }

    /* 运算符 / 标点 */
    const p = readPunctuator(src, i);
    if (p.text === "{") braceDepth += 1;
    if (p.text === "}") {
      if (stopAtBrace && braceDepth === 0) {
        /* } 属于 ${...} 的收尾，不输出，由调用方补上 */
        return { text: out.join(""), end: i + 1 };
      }
      braceDepth -= 1;
    }
    emit(p.text, tokStart, i + p.text.length, "punct");
    i += p.text.length;
  }

  if (stopAtBrace) throw new Error("模板字符串中未闭合的 ${");
  return { text: out.join(""), end: src.length };
}

function minifyJs(src) {
  return scanJs(src, 0, false).text;
}

/* ------------------------------------------------------------------ */
/* CSS 压缩：去注释、折叠空白，去掉 { } ; , 与 : 附近可安全删除的空白。 */
/* calc() / url() 内的空白保持不变（运算符两侧空格是语法必需）。       */
/* ------------------------------------------------------------------ */

function readCssString(src, i, quote) {
  let j = i + 1;
  while (j < src.length) {
    const c = src[j];
    if (c === "\\") {
      j += 2;
      continue;
    }
    if (c === quote) return { text: src.slice(i, j + 1), end: j + 1 };
    j += 1;
  }
  throw new Error("未闭合的 CSS 字符串");
}

function minifyCss(src) {
  const out = [];
  let pendingSpace = false;
  let i = 0;

  const lastChar = () => (out.length ? out[out.length - 1] : "");
  /* 这些字符前 / 后的空白可以安全删除 */
  const dropBefore = (c) => c === "{" || c === "}" || c === ";" || c === ",";
  const dropAfter = (c) => c === "{" || c === ";" || c === "," || c === ":";

  const flush = (cur) => {
    if (!pendingSpace) return;
    if (!(dropBefore(cur) || dropAfter(lastChar()))) out.push(" ");
    pendingSpace = false;
  };

  while (i < src.length) {
    const c = src[i];
    /* 注释：视为空白 */
    if (c === "/" && src[i + 1] === "*") {
      i += 2;
      while (i < src.length && !(src[i] === "*" && src[i + 1] === "/")) i += 1;
      i += 2;
      continue;
    }
    if (/\s/.test(c)) {
      pendingSpace = true;
      i += 1;
      continue;
    }
    if (c === '"' || c === "'") {
      flush(c);
      const s = readCssString(src, i, c);
      out.push(s.text);
      i = s.end;
      continue;
    }
    if (c === "}") {
      /* 去掉右花括号前多余的 ;（CSS 中可省略） */
      if (lastChar() === ";") out.pop();
      flush(c);
      out.push(c);
      i += 1;
      continue;
    }
    flush(c);
    out.push(c);
    i += 1;
  }
  return out.join("").replace(/\s+$/, "");
}

/* ------------------------------------------------------------------ */
/* HTML 压缩：去注释、折叠文本空白、压缩标签内属性空白。               */
/* <pre> / <textarea> / <script> / <style> 的内容原样保留。            */
/* ------------------------------------------------------------------ */

const RAW_HTML_TAGS = new Set(["script", "style", "pre", "textarea"]);

function findTagEnd(src, start) {
  let quote = "";
  for (let i = start; i < src.length; i++) {
    const c = src[i];
    if (quote) {
      if (c === quote) quote = "";
    } else if (c === '"' || c === "'") {
      quote = c;
    } else if (c === ">") {
      return i;
    }
  }
  throw new Error("未闭合的 HTML 标签");
}

/* 压缩单个标签：折叠属性间空白（引号内的值原样保留），去掉 > 前的空格 */
function minifyTag(tag) {
  const out = [];
  let pendingSpace = false;
  let quote = "";
  for (let i = 0; i < tag.length; i++) {
    const c = tag[i];
    if (quote) {
      out.push(c);
      if (c === quote) quote = "";
      continue;
    }
    if (c === '"' || c === "'") {
      if (pendingSpace && out.length) out.push(" ");
      pendingSpace = false;
      quote = c;
      out.push(c);
      continue;
    }
    if (/\s/.test(c)) {
      pendingSpace = true;
      continue;
    }
    if (pendingSpace && out.length) out.push(" ");
    pendingSpace = false;
    out.push(c);
  }
  return out
    .join("")
    .replace(/\s+\/?>$/, (m) => (m.includes("/") ? "/>" : ">"));
}

function minifyHtml(src) {
  const out = [];
  let i = 0;

  while (i < src.length) {
    /* 注释 */
    if (src.startsWith("<!--", i)) {
      const end = src.indexOf("-->", i + 4);
      if (end === -1) throw new Error("未闭合的 HTML 注释");
      i = end + 3;
      continue;
    }

    /* 标签 */
    if (src[i] === "<" && /[a-zA-Z!/]/.test(src[i + 1] || "")) {
      const tagEnd = findTagEnd(src, i);
      const raw = src.slice(i, tagEnd + 1);
      const minified = minifyTag(raw);
      out.push(minified);
      i = tagEnd + 1;

      const nameMatch = /^<\/?([a-zA-Z0-9]+)/.exec(raw);
      const tagName = nameMatch ? nameMatch[1].toLowerCase() : "";
      const isClosing = /^<\//.test(raw);
      const isSelfClosing = /\/>$/.test(minified);
      if (!isClosing && !isSelfClosing && RAW_HTML_TAGS.has(tagName)) {
        /* 原样保留原始内容直到闭合标签 */
        const closeStart = src.toLowerCase().indexOf(`</${tagName}`, i);
        if (closeStart === -1) throw new Error(`未闭合的 <${tagName}>`);
        out.push(src.slice(i, closeStart));
        i = closeStart;
      }
      continue;
    }

    /* 文本节点：把连续空白折叠成单个空格（浏览器本就会这样渲染） */
    let j = i;
    while (j < src.length && src[j] !== "<") j += 1;
    out.push(src.slice(i, j).replace(/[ \t\n\r\f]+/g, " "));
    i = j;
    /* 无法识别的 <（如文本中的小于号，非标签开头）：原样输出并前进，避免死循环 */
    if (
      i < src.length &&
      src[i] === "<" &&
      !/[a-zA-Z!/]/.test(src[i + 1] || "")
    ) {
      out.push("<");
      i += 1;
    }
  }
  return out.join("").replace(/\s+$/, "");
}

/* ------------------------------------------------------------------ */
/* 拷贝与压缩                                                         */
/* ------------------------------------------------------------------ */

function walkFiles(dir, cb) {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) walkFiles(full, cb);
    else if (entry.isFile()) cb(full);
  }
}

/* JS 语法自检：把压缩结果写到临时 .mjs 文件，用 node --check 校验 */
function validateJs(text, rel) {
  const tmp = path.join(
    os.tmpdir(),
    `showcase-deploy-${process.pid}-${Date.now()}.mjs`,
  );
  try {
    fs.writeFileSync(tmp, text, "utf8");
    execFileSync(process.execPath, ["--check", tmp], { stdio: "pipe" });
  } catch (err) {
    const detail = err.stderr ? err.stderr.toString() : err.message;
    throw new Error(`JS 压缩结果未通过语法校验（${rel}）：\n${detail}`);
  } finally {
    fs.rmSync(tmp, { force: true });
  }
}

function buildFile(srcFile, destFile, stats) {
  const ext = path.extname(srcFile).toLowerCase();
  const rel = path.relative(SHOWCASE, destFile);
  const srcSize = fs.statSync(srcFile).size;
  let content = fs.readFileSync(srcFile);

  if (MINIFY_EXT.has(ext)) {
    const text = content.toString("utf8");
    let minified;
    if (ext === ".html") minified = minifyHtml(text);
    else if (ext === ".css") minified = minifyCss(text);
    else {
      minified = minifyJs(text);
      validateJs(minified, rel);
    }
    content = Buffer.from(minified, "utf8");
  }

  fs.writeFileSync(destFile, content);
  const gzSize = zlib.gzipSync(content).length;
  stats.push({ rel, srcSize, outSize: content.length, gzSize });
}

function fmtBytes(n) {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(2)} MB`;
}

function main() {
  const resolvedShowcase = path.resolve(SHOWCASE);
  const resolvedPublic = path.resolve(PUBLIC);
  if (
    resolvedPublic !== path.join(resolvedShowcase, "public") ||
    !resolvedPublic.startsWith(resolvedShowcase + path.sep)
  ) {
    throw new Error(`public 目录必须位于 showcase 内：${resolvedPublic}`);
  }

  /* 清空旧产物，避免残留 */
  fs.rmSync(resolvedPublic, { recursive: true, force: true });
  fs.mkdirSync(resolvedPublic, { recursive: true });

  const stats = [];
  for (const rel of SOURCES) {
    const src = path.join(resolvedShowcase, rel);
    if (!fs.existsSync(src)) throw new Error(`缺少网站资源：${rel}`);
    if (fs.statSync(src).isDirectory()) {
      walkFiles(src, (file) => {
        const outFile = path.join(
          resolvedPublic,
          path.relative(resolvedShowcase, file),
        );
        fs.mkdirSync(path.dirname(outFile), { recursive: true });
        buildFile(file, outFile, stats);
      });
    } else {
      const dest = path.join(resolvedPublic, rel);
      fs.mkdirSync(path.dirname(dest), { recursive: true });
      buildFile(src, dest, stats);
    }
  }

  stats.sort((a, b) => a.rel.localeCompare(b.rel));
  const totalSrc = stats.reduce((s, x) => s + x.srcSize, 0);
  const totalOut = stats.reduce((s, x) => s + x.outSize, 0);
  const totalGz = stats.reduce((s, x) => s + x.gzSize, 0);
  const pct = totalSrc > 0 ? ((totalSrc - totalOut) / totalSrc) * 100 : 0;

  const rows = stats.map((x) => {
    const per =
      x.srcSize > 0
        ? `-${Math.round((1 - x.outSize / x.srcSize) * 100)}%`
        : "-";
    return `${x.rel.padEnd(30)}${fmtBytes(x.srcSize).padStart(11)} → ${fmtBytes(x.outSize).padStart(11)}  ${per.padStart(5)}  gzip ${fmtBytes(x.gzSize).padStart(11)}`;
  });

  console.log(`已生成 public/（${stats.length} 个文件）`);
  console.log(rows.join("\n"));
  console.log(
    `合计 ${fmtBytes(totalSrc)} → ${fmtBytes(totalOut)}（节省 ${fmtBytes(totalSrc - totalOut)}，-${pct.toFixed(1)}%），gzip 后 ${fmtBytes(totalGz)}`,
  );
  console.log(
    "public/ 已就绪，可直接上传到任意静态服务器（如 Nginx / GitHub Pages / 对象存储）。",
  );
}

if (
  process.argv[1] &&
  path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)
) {
  main();
}

export { minifyHtml, minifyCss, minifyJs };
