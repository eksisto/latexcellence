import { BrowserRouter, Routes, Route } from "react-router-dom";
import { TooltipProvider } from "@/components/ui/tooltip";
import ThemeToggle from "@/components/ThemeToggle";
import WelcomePage from "./pages/WelcomePage";
import WorkspacePage from "./pages/WorkspacePage";
import FormulaOCRPage from "./pages/FormulaOCRPage";
import TemplateGenPage from "./pages/TemplateGenPage";
import TypoCheckPage from "./pages/TypoCheckPage";
import ExpressionOptPage from "./pages/ExpressionOptPage";

export default function App() {
  return (
    <TooltipProvider>
      <BrowserRouter>
        <div className="fixed top-4 right-4 z-50">
          <ThemeToggle />
        </div>
        <Routes>
          <Route path="/" element={<WelcomePage />} />
          <Route path="/workspace" element={<WorkspacePage />} />
          <Route path="/formula-ocr" element={<FormulaOCRPage />} />
          <Route path="/template-gen" element={<TemplateGenPage />} />
          <Route path="/typo-check" element={<TypoCheckPage />} />
          <Route path="/expression-opt" element={<ExpressionOptPage />} />
        </Routes>
      </BrowserRouter>
    </TooltipProvider>
  );
}
