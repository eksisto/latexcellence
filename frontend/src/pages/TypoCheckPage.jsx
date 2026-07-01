import { Link } from "react-router-dom";

export default function TypoCheckPage() {
  /**
   * 错字查找页面
   *
   * 工作流程：
   *   1. 选择已打开的 LaTeX 项目文件
   *   2. 后端解析 .tex → 提取段落纯文本 → 发送给 LLM
   *   3. LLM 返回每段的修改建议（JSON 格式）
   *   4. 前端展示原文和修改建议（DiffViewer 组件）
   *   5. 用户逐项确认/拒绝修改
   *   6. 确认后调用 apply_change 原地修改文件
   *
   * 关键设计：
   *   - 修改前自动备份（.bak 文件）
   *   - 检查文件 MD5 防止审核过程中被外部修改
   *   - 支持 SSE 流式进度显示
   */
  return (
    <div className="min-h-screen p-8">
      <div className="max-w-4xl mx-auto">
        <div className="flex items-center gap-4 mb-8">
          <Link to="/" className="text-blue-600 hover:underline text-sm">
            &larr; Home
          </Link>
          <h1 className="text-3xl font-bold">Typo Check</h1>
        </div>
        <div className="bg-white rounded-xl shadow-md border border-gray-100 p-8 text-center">
          <p className="text-gray-400 text-lg">
            Open a project first to check for typos
          </p>
          <p className="text-gray-300 text-sm mt-2">
            Paragraphs will be scanned by AI and reviewed by you
          </p>
        </div>
      </div>
    </div>
  );
}
