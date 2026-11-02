/**
 * 段落查询复合键（逐段检查族共用）。
 *
 * 段落号按文件各自从 0 计，多文件（含子文件检查）模式下仅按 index
 * 建键会同号跨文件碰撞（Map 后写覆盖先写），段落上下文会渲染成
 * 别的文件的内容。
 */
export function paragraphKey(filePath, index) {
  return `${filePath || ""}:${index}`;
}

/** 按 file_path + index 复合键构建段落查询 Map */
export function buildParagraphMap(paragraphs) {
  return new Map(
    paragraphs.map((p) => [paragraphKey(p.file_path, p.index), p]),
  );
}
