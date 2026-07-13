import { BrowserRouter, Routes, Route } from "react-router-dom";
import { TooltipProvider } from "@/components/ui/tooltip";
import ThemeToggle from "@/components/ThemeToggle";
import SettingsDialog from "@/components/SettingsDialog";
import useFavicon from "./hooks/useFavicon";
import WelcomePage from "./pages/WelcomePage";
import WorkspacePage from "./pages/WorkspacePage";
import FormulaOCRPage from "./pages/FormulaOCRPage";
import TableOCRPage from "./pages/TableOCRPage";
import StyleModifyPage from "./pages/StyleModifyPage";
import TemplateGenPage from "./pages/TemplateGenPage";
import TypoCheckPage from "./pages/TypoCheckPage";
import ExpressionOptPage from "./pages/ExpressionOptPage";
import FormattingPage from "./pages/FormattingPage";
import MarkdownConvertPage from "./pages/MarkdownConvertPage";

export default function App() {
  useFavicon();

  return (
    <TooltipProvider>
      <BrowserRouter>
        <div className="fixed top-4 right-4 z-50 flex items-center gap-1">
          <SettingsDialog />
          <ThemeToggle />
        </div>
        <Routes>
          <Route path="/" element={<WelcomePage />} />
          <Route path="/workspace" element={<WorkspacePage />} />
          <Route path="/formula-ocr" element={<FormulaOCRPage />} />
          <Route path="/table-ocr" element={<TableOCRPage />} />
          <Route path="/style-modify" element={<StyleModifyPage />} />
          <Route path="/template-gen" element={<TemplateGenPage />} />
          <Route path="/typo-check" element={<TypoCheckPage />} />
          <Route path="/expression-opt" element={<ExpressionOptPage />} />
          <Route path="/formatting" element={<FormattingPage />} />
          <Route path="/markdown-convert" element={<MarkdownConvertPage />} />
        </Routes>
      </BrowserRouter>
    </TooltipProvider>
  );
}
