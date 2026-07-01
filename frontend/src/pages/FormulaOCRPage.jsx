import { Link } from "react-router-dom";

export default function FormulaOCRPage() {
  /**
   * 公式识别页面
   *
   * 功能：
   *   - 拖拽或点击上传数学公式图片
   *   - 后端调用 LLM Vision API 识别为 LaTeX 代码
   *   - 显示识别结果和置信度
   *
   * 后续实现：
   *   - 拖拽上传区域（react-dropzone 或原生拖拽）
   *   - 图片预览
   *   - LaTeX 公式渲染预览（KaTeX）
   *   - 置信度不足时提示人工复核
   *   - 复制/插入到文档
   */
  return (
    <div className="min-h-screen p-8">
      <div className="max-w-2xl mx-auto">
        <div className="flex items-center gap-4 mb-8">
          <Link to="/" className="text-blue-600 hover:underline text-sm">
            &larr; Home
          </Link>
          <h1 className="text-3xl font-bold">Formula OCR</h1>
        </div>

        {/* 图片上传区域 */}
        <div className="bg-white rounded-xl shadow-md border border-gray-100 p-8">
          <div className="border-2 border-dashed border-gray-200 rounded-lg p-12 text-center cursor-pointer hover:border-blue-400 transition-colors">
            <div className="text-4xl mb-3">📷</div>
            <p className="text-gray-500">
              Click or drag an image to upload
            </p>
            <p className="text-gray-300 text-sm mt-1">
              PNG, JPEG, WebP up to 10MB
            </p>
          </div>
        </div>
      </div>
    </div>
  );
}
