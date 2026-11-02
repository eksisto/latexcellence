/**
 * 逐段检查结果卡 DOM id 契约（useResultFilter 跳转与各检查页结果卡共用）。
 *
 * 键必须含 file_path：段落号按文件各自从 0 计，多文件（含子文件检查）
 * 模式下不同文件的同号段落互不相同，仅按段落号建 id 会互跳。
 * id 中出现 `/` 与 `:` 对 getElementById 全匹配合法，无须转义。
 */
export function resultDomId(idPrefix, filePath, paragraphIndex) {
  return `${idPrefix}-P${filePath || ""}:${paragraphIndex}`;
}
