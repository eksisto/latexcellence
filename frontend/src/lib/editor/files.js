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
 * 判断内容是否超过编辑器可打开的大小上限。
 * @param {string|number} value 文件内容或其字符数
 * @returns {boolean}
 */
export function isFileTooLarge(value) {
  const length = typeof value === "number" ? value : (value?.length ?? 0);
  return length > MAX_EDITOR_FILE_CHARS;
}
