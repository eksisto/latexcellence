import { Link } from "react-router-dom";

export default function TemplateGenPage() {
  /**
   * 模板生成页面
   *
   * 功能：
   *   - 用户输入自然语言描述（如"IEEE 会议论文，双栏，带摘要和参考文献"）
   *   - 后端调用 LLM 生成完整的 LaTeX 模板源码
   *   - 展示生成的模板并提供"应用到项目"按钮
   *
   * 后续实现：
   *   - 模板代码预览（带语法高亮）
   *   - 模板名称和保存功能
   *   - 预置模板选择
   */
  return (
    <div className="min-h-screen p-8">
      <div className="max-w-2xl mx-auto">
        <div className="flex items-center gap-4 mb-8">
          <Link to="/" className="text-blue-600 hover:underline text-sm">
            &larr; Home
          </Link>
          <h1 className="text-3xl font-bold">Template Generator</h1>
        </div>

        {/* 输入区：用户描述想要的模板样式 */}
        <div className="bg-white rounded-xl shadow-md border border-gray-100 p-8">
          <textarea
            className="w-full h-32 border border-gray-200 rounded-lg p-4 resize-none focus:outline-none focus:ring-2 focus:ring-blue-400"
            placeholder={
              "Describe the template style you want, e.g.:\n" +
              "IEEE conference paper with two-column layout, abstract, " +
              "and bibliography..."
            }
          />
          <button className="mt-4 px-6 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors">
            Generate Template
          </button>
        </div>
      </div>
    </div>
  );
}
