/**
 * PromptEditor 组件 —— 自定义提示词编辑器
 *
 * 用于表达优化模块，允许用户编写/修改发送给 LLM 的提示词。
 * 提示词以文件形式存储在 prompts/expression/ 目录中，
 * 通过 backend/modules/expression_opt/prompt_manager.py 管理。
 *
 * props:
 *   @param {string}   prompt   - 当前提示词内容
 *   @param {Function} onSave   - 保存回调 (newPrompt) => void
 *
 * 后续实现：
 *   - 提示词历史版本管理
 *   - 多语言支持
 *   - 预设提示词模板（下拉选择）
 *   - 变量注入功能（如 {{paragraph}} 代表当前段落）
 */
export default function PromptEditor({ prompt, onSave }) {
  return (
    <div className="border border-gray-200 rounded-lg p-4 bg-white">
      <h3 className="font-semibold text-sm text-gray-500 mb-3">
        Custom Prompt
      </h3>
      <textarea
        className="w-full h-24 border border-gray-200 rounded p-3 text-sm resize-none focus:outline-none focus:ring-2 focus:ring-blue-400"
        value={prompt || ""}
        onChange={(e) => onSave?.(e.target.value)}
        placeholder="Enter your custom prompt..."
      />
    </div>
  );
}
