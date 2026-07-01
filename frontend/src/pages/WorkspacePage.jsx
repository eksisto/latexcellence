import { Link } from "react-router-dom";

export default function WorkspacePage() {
  /**
   * 项目工作台页面
   *
   * 功能（后续实现）：
   *   - 左侧 ProjectExplorer 文件树
   *   - 中间代码编辑器
   *   - 右侧 PDF 预览面板
   *   - 错字查找、表达优化等操作入口
   */
  return (
    <div className="min-h-screen p-8">
      <div className="max-w-4xl mx-auto">
        <div className="flex items-center gap-4 mb-8">
          <Link to="/" className="text-blue-600 hover:underline text-sm">
            &larr; Home
          </Link>
          <h1 className="text-3xl font-bold">Project Workspace</h1>
        </div>
        <div className="bg-white rounded-xl shadow-md border border-gray-100 p-8 text-center">
          <p className="text-gray-400 text-lg">
            Open a LaTeX project to get started
          </p>
          <p className="text-gray-300 text-sm mt-2">
            Project management and PDF preview will be available here
          </p>
        </div>
      </div>
    </div>
  );
}
