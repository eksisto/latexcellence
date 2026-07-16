import { lazy, Suspense } from "react";
import { BrowserRouter, Routes, Route } from "react-router-dom";
import { TooltipProvider } from "@/components/ui/tooltip";
import { Toaster } from "@/components/ui/sonner";
import { Loader2 } from "lucide-react";
import ThemeToggle from "@/components/ThemeToggle";
import SettingsDialog from "@/components/SettingsDialog";
import useFavicon from "./hooks/useFavicon";

const WelcomePage = lazy(() => import("./pages/WelcomePage"));
const WorkspacePage = lazy(() => import("./pages/WorkspacePage"));
const FormulaOCRPage = lazy(() => import("./pages/FormulaOCRPage"));
const TableOCRPage = lazy(() => import("./pages/TableOCRPage"));
const StyleModifyPage = lazy(() => import("./pages/StyleModifyPage"));
const TemplateGenPage = lazy(() => import("./pages/TemplateGenPage"));
const TypoCheckPage = lazy(() => import("./pages/TypoCheckPage"));
const ExpressionOptPage = lazy(() => import("./pages/ExpressionOptPage"));
const FormattingPage = lazy(() => import("./pages/FormattingPage"));
const MarkdownConvertPage = lazy(() => import("./pages/MarkdownConvertPage"));
const GitDiffPage = lazy(() => import("./pages/GitDiffPage"));
const TerminologyCheckPage = lazy(() => import("./pages/TerminologyCheckPage"));
const WritingChatPage = lazy(() => import("./pages/WritingChatPage"));
const BibManagerPage = lazy(() => import("./pages/BibManagerPage"));

export default function App() {
  useFavicon();

  return (
    <TooltipProvider>
      <BrowserRouter>
        <div className="fixed top-4 right-4 z-50 flex items-center gap-1">
          <SettingsDialog />
          <ThemeToggle />
        </div>
        <Toaster />
        <Suspense
          fallback={
            <div className="flex items-center justify-center min-h-screen">
              <Loader2 className="size-6 animate-spin text-muted-foreground" />
            </div>
          }
        >
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
            <Route path="/git-diff" element={<GitDiffPage />} />
            <Route path="/terminology-check" element={<TerminologyCheckPage />} />
            <Route path="/writing-chat" element={<WritingChatPage />} />
            <Route path="/bib-manager" element={<BibManagerPage />} />
          </Routes>
        </Suspense>
      </BrowserRouter>
    </TooltipProvider>
  );
}
