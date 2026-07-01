/**
 * DiffViewer 组件 —— 修改对比
 *
 * 核心 UI 组件，用于对比原文和 LLM 提出的修改建议。
 * 用户在此组件中逐项审核并确认/拒绝修改。
 *
 * props:
 *   @param {string}   original   - 原始文本
 *   @param {string}   modified   - LLM 修改后的文本
 *   @param {Array}    changes    - 修改详情列表
 *     @param {string} changes[].type       - 修改类型（"typo" / "rewrite"）
 *     @param {string} changes[].original   - 被修改的部分原文
 *     @param {string} changes[].corrected  - 修正后的内容
 *     @param {string} changes[].explanation - 修改原因/解释
 *
 * 后续实现：
 *   - 行内差异高亮（逐字比对）
 *   - 并排对比（原文 vs 修改版）
 *   - 每项修改的"确认/拒绝"按钮
 *   - "全部接受" / "全部拒绝" 批量操作
 *   - 支持键盘快捷键操作
 */
export default function DiffViewer({ original, modified, changes = [] }) {
  return (
    <div className="border border-gray-200 rounded-lg p-4 bg-white">
      <div className="text-sm text-gray-400 mb-2">
        Diff viewer — coming soon
      </div>
      <div className="text-gray-600">{original || "No content"}</div>
    </div>
  );
}
