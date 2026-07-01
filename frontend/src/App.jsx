/**
 * 根组件 —— 定义应用路由
 *
 * 所有页面通过 react-router-dom 的 BrowserRouter 管理路由：
 *   /                → 欢迎页（功能导航）
 *   /workspace       → 项目工作台（文件树 + PDF 预览）
 *   /formula-ocr     → 公式识别
 *   /template-gen    → 模板生成
 *   /typo-check      → 错字查找（带 diff 审核）
 *   /expression-opt  → 表达优化（带 diff 审核）
 */
import { BrowserRouter, Routes, Route } from "react-router-dom";
import WelcomePage from "./pages/WelcomePage";
import WorkspacePage from "./pages/WorkspacePage";
import FormulaOCRPage from "./pages/FormulaOCRPage";
import TemplateGenPage from "./pages/TemplateGenPage";
import TypoCheckPage from "./pages/TypoCheckPage";
import ExpressionOptPage from "./pages/ExpressionOptPage";

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<WelcomePage />} />
        <Route path="/workspace" element={<WorkspacePage />} />
        <Route path="/formula-ocr" element={<FormulaOCRPage />} />
        <Route path="/template-gen" element={<TemplateGenPage />} />
        <Route path="/typo-check" element={<TypoCheckPage />} />
        <Route path="/expression-opt" element={<ExpressionOptPage />} />
      </Routes>
    </BrowserRouter>
  );
}
