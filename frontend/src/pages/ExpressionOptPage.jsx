import { Link } from "react-router-dom";

export default function ExpressionOptPage() {
  /**
   * 表达优化页面
   *
   * 工作流程：
   *   1. 选择已打开的 LaTeX 项目文件
   *   2. 后端解析 .tex → 提取段落纯文本 → 发送给 LLM
   *   3. LLM 返回优化后的文本（JSON 格式）
   *   4. 前端展示原文和优化建议（DiffViewer 组件）
   *   5. 用户逐项确认/拒绝
   *   6. 确认后调用 apply_change 原地修改
   *
   * 自定义提示词：
   *   - 用户可以通过 PromptEditor 组件编写自定义优化提示词
   *   - 提示词存放在 prompts/expression/ 目录
   *   - 后端通过 prompt_manager.py 管理
   */
  return (
    <div className="min-h-screen p-8">
      <div className="max-w-4xl mx-auto">
        <div className="flex items-center gap-4 mb-8">
          <Link to="/" className="text-blue-600 hover:underline text-sm">
            &larr; Home
          </Link>
          <h1 className="text-3xl font-bold">Expression Optimization</h1>
        </div>

        <div className="bg-white rounded-xl shadow-md border border-gray-100 p-8 text-center">
          <p className="text-gray-400 text-lg">
            Open a project first to optimize expressions
          </p>
          <p className="text-gray-300 text-sm mt-2">
            AI will suggest improvements to your academic writing style
          </p>
        </div>
      </div>
    </div>
  );
}
