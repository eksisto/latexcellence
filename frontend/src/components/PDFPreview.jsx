/**
 * PDFPreview 组件 —— 编译后的 PDF 预览
 *
 * 嵌入 iframe 显示 LaTeX 编译后的 PDF 文件。
 * 编译由后端调用本地 TeX 发行版（xelatex/pdflatex）完成。
 *
 * props:
 *   @param {string|null} url - PDF 文件的访问 URL（后端提供静态文件路径）
 *                               为 null 时显示占位提示
 *
 * 后续实现：
 *   - PDF.js 原生渲染（替代 iframe，更好控制）
 *   - 页面导航（上一页/下一页/跳转）
 *   - 缩放控制
 *   - 同步滚动（点击左侧源文件行号，跳转到 PDF 对应位置）
 *   - 编译进度提示
 */
export default function PDFPreview({ url }) {
  if (!url) {
    return (
      <div className="border border-gray-200 rounded-lg p-8 bg-white text-center h-full flex items-center justify-center">
        <p className="text-gray-300">No PDF to preview</p>
      </div>
    );
  }
  return (
    <div className="border border-gray-200 rounded-lg overflow-hidden bg-white h-full">
      <iframe src={url} className="w-full h-full" title="PDF Preview" />
    </div>
  );
}
