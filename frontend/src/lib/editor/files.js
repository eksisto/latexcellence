/**
 * 编辑器文件边界纯模块（editor-design §2 / 决策 Q3、§4 文件上限）。
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

/**
 * 校验「新建文件」的文件名输入（文件树新建入口，与后端 create-file
 * 白名单同源）。单段文件名：禁止路径分隔符与点号目录引用（目标目录
 * 由树节点决定，不允许在文件名里夹带路径）。
 *
 * @param {string} name 用户输入的文件名
 * @returns {string|null} null 表示合法；否则返回错误码
 *   "empty" / "separator" / "suffix"（供 i18n key 映射）
 */
export function validateNewFileName(name) {
  if (typeof name !== "string") return "empty";
  const trimmed = name.trim();
  if (trimmed.length === 0) return "empty";
  if (/[/\\]/.test(trimmed) || trimmed === "." || trimmed === "..") {
    return "separator";
  }
  if (!isEditableFilePath(trimmed)) return "suffix";
  return null;
}

/**
 * 拼接目标目录相对路径与文件名（目录为空 = 项目根，返回文件名本身）。
 * @param {string|null} dirPath 树中目录节点的相对路径（根目录为 null/空）
 * @param {string} fileName 已通过 validateNewFileName 的单段文件名
 * @returns {string}
 */
export function joinProjectPath(dirPath, fileName) {
  const dir = typeof dirPath === "string" ? dirPath.replace(/\/+$/, "") : "";
  if (!dir) return fileName;
  return `${dir}/${fileName}`;
}

/**
 * 校验「上传文件」的原始文件名（文件树上传入口，editor-file-upload-design
 * §3-6 / §5.5）。与后端 _validate_upload_target_name / _reject_hidden_components
 * 同口径：拒绝空名、路径分隔符、控制字符与点开头（隐藏文件）。任意后缀
 * 均可上传（决策 D1），不做扩展名白名单；服务端校验仍为权威，此处仅
 * 供选择框回调预检、避免无效请求。
 *
 * @param {string} name File.name（浏览器原始文件名）
 * @returns {string|null} null 表示合法；否则返回错误码
 *   "empty" / "separator" / "control" / "hidden"（供 i18n key 映射）
 */
export function validateUploadFileName(name) {
  if (typeof name !== "string" || name.trim().length === 0) return "empty";
  if (/[/\\]/.test(name)) return "separator";
  if (/[\u0000-\u001F\u007F]/.test(name)) return "control";
  if (name.startsWith(".")) return "hidden";
  return null;
}
