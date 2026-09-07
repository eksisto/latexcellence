// 拷贝 pdfjs-dist 内置预置 CMap 资源到 public/pdfjs/cmaps（生成物不入库）。
//
// 背景：pdf.js 翻译 xdvipdfmx 嵌入的无 ToUnicode CID 字体（如 ctex 默认
// 的 Fandol 中文字体）时，需按字体 /CIDSystemInfo 拉取预置 CMap（实测
// 请求 Adobe-GB1-UCS2）；未提供 cMapUrl 时 translateFont 整体失败，中文
// 字形在 PdfJsPreview 中不渲染（桌面查看器直接按 CID 绘制字形，不受影
// 响）。与 latexGrammar parser 生成物同一模式：经 predev/prebuild/pretest
// 钩子先行生成。
import { cpSync, existsSync, readdirSync, rmSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

// 脚本位于 src/lib/editor/ 下，上溯三级到 frontend 根
const frontendRoot = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "../..",
  "..",
);
const src = path.join(frontendRoot, "node_modules", "pdfjs-dist", "cmaps");
const dest = path.join(frontendRoot, "public", "pdfjs", "cmaps");

if (!existsSync(src)) {
  console.warn(
    "[copy-pdfjs-cmaps] skip: pdfjs-dist/cmaps not found (dependencies not installed?)",
  );
  process.exit(0);
}

rmSync(dest, { recursive: true, force: true });
cpSync(src, dest, { recursive: true });
const count = readdirSync(dest).length;
console.log(
  `[copy-pdfjs-cmaps] copied ${count} packed CMap files -> public/pdfjs/cmaps`,
);
