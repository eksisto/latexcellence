/**
 * 分组顺序即写作流程，因此「§ 编号」是真实的、可编码顺序的：
 * 内容导入(1) → 内容生成(2) → 文本审查与优化(3) → 代码审查与排版(4) → 版本控制(5) → AI 辅助(6)。
 *
 * 欢迎页点线目录与各工具页 PageHeader 的「§N · 分组」kicker 共用此映射，
 * 编号必须两端一致，新增 / 重排分组时需同步修改此处。
 */
export const SECTION_BY_GROUP = {
  contentImport: 1,
  contentGeneration: 2,
  textReview: 3,
  code_review: 4,
  versionControl: 5,
  aiAssist: 6,
};
