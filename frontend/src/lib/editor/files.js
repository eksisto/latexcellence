/**
 * 编辑器文件边界纯模块（editor-plan §2 / 决策 Q3、§4 文件上限）。
 *
 * 职责：
 *   1. 可编辑扩展名白名单（与后端 backend/modules/editor/service.py 的
 *      EDITABLE_SUFFIXES 保持一致），做成常量便于扩展；
 *   2. 文件大小上限：超过约 2MB 的内容拒绝进入 CM6 缓冲区，防止浏览器端卡顿。
 *
 * 全部为纯函数，供 node --test 直接单测；页面层只做调用不内联判断。
 */

/** 可编辑文件扩展名白名单（决策 Q3；须与后端白名单同步） */
export const EDITABLE_SUFFIXES = [".tex", ".bib", ".sty", ".cls"];

/**
 * 可内嵌预览的位图扩展名（浏览器原生 <img> 可渲染；须与后端
 * backend/modules/editor/service.py 的 PREVIEWABLE_SUFFIXES 同步）。
 */
export const IMAGE_PREVIEW_SUFFIXES = [
  ".png",
  ".jpg",
  ".jpeg",
  ".gif",
  ".webp",
  ".bmp",
];

/**
 * 可在编辑器页内预览的白名单（PDF 内嵌 + 常见位图；须与后端
 * PREVIEWABLE_SUFFIXES 同步）。刻意不收 .svg（inline 提供用户上传的
 * SVG 有存储型 XSS 风险）与 .eps（浏览器无法原生渲染）。
 */
export const PREVIEWABLE_SUFFIXES = [".pdf", ...IMAGE_PREVIEW_SUFFIXES];

/** 编辑器打开的字符数上限（约 2MB：LaTeX 源码以 ASCII 为主，字符数≈字节数） */
export const MAX_EDITOR_FILE_CHARS = 2_000_000;

/**
 * 判断路径是否在可编辑白名单内（大小写不敏感）。
 * @param {string} filePath 项目相对路径
 * @returns {boolean}
 */
export function isEditableFilePath(filePath) {
  if (typeof filePath !== "string" || filePath.length === 0) return false;
  const lower = filePath.toLowerCase();
  return EDITABLE_SUFFIXES.some((suffix) => lower.endsWith(suffix));
}

/**
 * 判断路径是否在可预览白名单内（大小写不敏感）。
 * @param {string} filePath 项目相对路径
 * @returns {boolean}
 */
export function isPreviewableFilePath(filePath) {
  if (typeof filePath !== "string" || filePath.length === 0) return false;
  const lower = filePath.toLowerCase();
  return PREVIEWABLE_SUFFIXES.some((suffix) => lower.endsWith(suffix));
}

/**
 * 判断路径是否属于位图预览（<img> 渲染；否则 PDF 走 iframe）。
 * @param {string} filePath 项目相对路径
 * @returns {boolean}
 */
export function isImagePreviewFilePath(filePath) {
  if (typeof filePath !== "string" || filePath.length === 0) return false;
  const lower = filePath.toLowerCase();
  return IMAGE_PREVIEW_SUFFIXES.some((suffix) => lower.endsWith(suffix));
}

/**
 * 判断内容是否超过编辑器可打开的大小上限。
 * @param {string|number} value 文件内容或其字符数
 * @returns {boolean}
 */
export function isFileTooLarge(value) {
  const length = typeof value === "number" ? value : (value?.length ?? 0);
  return length > MAX_EDITOR_FILE_CHARS;
}
