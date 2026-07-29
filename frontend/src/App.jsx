import { lazy, Suspense, memo, useCallback } from "react";
import {
  BrowserRouter,
  Routes,
  Route,
  Navigate,
  useNavigate,
} from "react-router-dom";
import {
  TooltipProvider,
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { Button } from "@/components/ui/button";
import { Toaster } from "@/components/ui/sonner";
import { Loader2, Archive } from "lucide-react";
import { useTranslation } from "react-i18next";
import ThemeToggle from "@/components/ThemeToggle";
import ThemeSwitcher from "@/components/ThemeSwitcher";
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
const CodeReviewPage = lazy(() => import("./pages/CodeReviewPage"));
const FormatConvertPage = lazy(() => import("./pages/FormatConvertPage"));
const GitDiffPage = lazy(() => import("./pages/GitDiffPage"));
const TerminologyCheckPage = lazy(() => import("./pages/TerminologyCheckPage"));
const ParagraphTranslatePage = lazy(
  () => import("./pages/ParagraphTranslatePage"),
);
const WritingChatPage = lazy(() => import("./pages/WritingChatPage"));
const BibManagerPage = lazy(() => import("./pages/BibManagerPage"));

const bgSvg = (
  <svg
    className="fixed bottom-20 -right-8 -z-10 h-[50vh] w-auto opacity-[0.24] pointer-events-none overflow-visible"
    xmlns="http://www.w3.org/2000/svg"
    viewBox="0 0 1176.7 877.08"
  >
    <circle class="a" cx="427.5" cy="427.5" r="427.5" fill="var(--primary)" />
    <path
      d="M802.46,652.09v2.5c0,4.6-.06,8.84-.23,12.75-.58,13.12-2.37,22.43-7.1,28.77-4.18,5.63-10.82,9.12-21.08,10.82q-2.26,2.85-4.59,5.64c32.75-3.42,37.65-20.29,38-53.76,0-.57,0-1.14,0-1.72h2.72V777h-2.72c-.2-37.36-5.23-55-44.37-56.95-1.4,1.62-2.82,3.24-4.28,4.84,19.38.39,30.33,4.14,36.31,12.31,6.14,8.4,7.34,21.87,7.34,42.3V782h12.72V652.09Zm93.38,111.07-.32,2.13c-5.14,34.79-9.21,62.28-22.43,80.57-13.61,18.83-37.14,27.22-76.31,27.22H730.12c-17.59,0-17.59-1.24-17.59-16.44V769.46q-2.47,2.07-5,4.07v83.11c0,17.65,2,21.44,22.59,21.44h66.66c40.94,0,65.72-9,80.36-29.29,13.66-18.9,17.91-46.17,23-80.63h3.59L887.19,884.54H641.93v-6.6c31.81,0,39.47-.6,39.47-24.09V792.92c-1.65,1.15-3.32,2.27-5,3.38v57.55c0,19.09-2.79,19.09-37,19.09h-2.5v16.6h254.6l18-126.38Zm-11-205.78H851.32q-.71,2.5-1.46,5h30.51l11,101.23h-3.62c-3.08-28.48-6.88-53.29-19.81-69.4-5.75-7.16-13.26-12.6-23-16.43q-.81,2.38-1.65,4.74c8.8,3.51,15.57,8.41,20.72,14.82,11.29,14.07,15.38,35.11,19,69l.24,2.24h13.69Zm-82.63,110c-.58,13.12-2.37,22.43-7.1,28.77-4.18,5.63-10.82,9.12-21.08,10.82q-2.26,2.85-4.59,5.64c32.75-3.42,37.65-20.29,38-53.76C805.75,661.68,804,664.52,802.23,667.34Z"
      fill="var(--primary-foreground-inverse)"
      transform="translate(-12.65 -12.46)"
    />
    <path
      d="M1189.35,785.34l-2.7-.21c-11.56-.9-35.2-.9-47.9-.9-15.62,0-39.24,0-54.54.91l-2.65.15V768.68l2.35-.14c15.32-.89,18.77-8.08,18.77-10.53,0-1,0-2.08-3.32-6.74l-81.52-119.82-74.75,109.1c-2.24,3.16-4.33,6.11-4.33,11.62,0,4.41,1.64,14.83,16.9,16.3l2.26.22v16.57l-2.69-.21c-11.87-.9-34.06-.9-47.31-.9-11.1,0-29.69,0-40.56.9l-2.71.23V768.46h2.5c32,0,49.72-15.93,58.94-29.29l82.58-120.64L917,483.87c-9.63-13.53-16.37-14.81-43.56-14.81h-2.5v-16.8l2.7.21c11.5.9,35.14.9,47.84.9,15.63,0,39.24,0,54.55-.91l2.65-.15V469l-2.43.07c-14.14.4-18.71,7.22-18.71,10.46,0,.76.26,1.91,3.41,6.84l69,100.94,61.34-89c3.12-4.37,5.31-7.84,5.31-13,0-6.72-2.93-15-16.89-16.31l-2.27-.21V452.26l2.69.21c11.87.9,31.52.9,47.31.9,11.09,0,29.66,0,40.56-.9l2.71-.23V469l-2.47,0c-38,.51-52.1,20.55-58.9,30.18l-.09.13-70.07,101.3,104.63,153.06c8.86,12.58,12.85,14.82,43.09,14.82h2.5Zm-102.79-12V780c15.41-.77,37.38-.77,52.19-.77,11.86,0,33.2,0,45.6.73v-6.43c-28.48-.12-34.6-2.62-44.7-16.95L1033.07,600.64l1-1.41,71.17-102.89c7.12-10.09,21.78-30.87,60.45-32.23v-6.46c-11.16.72-27.3.72-38.27.72-14.74,0-32.83,0-45-.73v6.71c17.24,2.62,19.16,15.44,19.16,21,0,6.58-2.82,11.07-6.22,15.84l-65.45,94.93-73.11-107c-3.3-5.17-4.24-7.31-4.24-9.6,0-5.44,5.79-14.08,21.14-15.32V457.6c-15.42.77-37.38.77-52.2.77-11.85,0-33.18,0-45.54-.73v6.42c24.58.07,34.05,1.34,45.16,17l93.64,137.52-1,1.41L930.21,742c-9.67,14-28,30.62-60.56,31.43v6.44c11.14-.72,27.29-.72,38.27-.72,12.39,0,32.53,0,45,.74v-6.73c-14.08-2.27-19.16-12.16-19.16-21,0-7.1,2.78-11,5.23-14.48l78.87-115.11,85.61,125.84c3.91,5.49,4.21,7.44,4.21,9.59C1107.68,762.69,1103.19,771.62,1086.56,773.35Z"
      fill="var(--primary-foreground-inverse)"
      transform="translate(-12.65 -12.46)"
    />
    <path
      d="M906.62,765.66H898c-10.53,71.21-16.73,109.92-101.21,109.92H730.12c-19.18,0-20.09-2.42-20.09-18.94V722.39h45.31c45.31,0,49.63,16.6,49.63,57.14h7.71V654.59H805c0,39.85-4.33,56.14-49.63,56.14H710v-119c0-16.22.92-18.64,20.09-18.64h65.67c74.32,0,83,29.54,89.7,93h8.65L882.61,559.88H639.43v13.19c34,0,39.47,0,39.47,21.59V853.85c0,21.59-5.38,21.59-39.47,21.59V887H889.36Z"
      fill="var(--primary-foreground)"
      transform="translate(-12.65 -12.46)"
    />
    <path
      d="M676.21,564.37H662.56l-.18-2.31C656.26,483.5,650,470.89,578,470.89H551.93c-6.16,1.48-6.44,4.83-6.44,15.82V748.15c0,15.48,0,20.15,38.79,20.15h15.9V785l-3.15-.06c-22.95-.44-46.69-.9-69.94-.9s-46.87.46-69.72.9L454,785V768.3h16.14c39.41,0,39.41-4.67,39.41-20.15V486.71c0-11.59-.28-14.74-6.88-15.83H476.33c-72.61,0-78.88,12.61-85,91.17l-.18,2.31H377.49L386,453.14H668Z"
      fill="var(--primary-foreground)"
      transform="translate(-12.65 -12.46)"
    />
    <path
      d="M1036.1,600.65l71.06-102.73c7.11-10.09,21.59-30.84,61-31.37V455c-11,.91-29.24.91-40.76.91-15.83,0-35.53,0-47.5-.91v11.6c15.38,1.44,19.16,11.05,19.16,18.8,0,5.74-2.35,9.61-5.76,14.38l-63.4,92-71.06-104c-3.33-5.23-3.8-6.75-3.8-8.19,0-4.33,5.23-12.5,21.14-13V455c-15.38.91-38.87.91-54.7.91-12.43,0-36.43,0-48-.91v11.6c26.38,0,35,1,45.62,15.91l92.65,136.07L928.15,740.59c-20.61,29.85-51.82,30.37-61,30.37v11.6c11-.91,29.25-.91,40.77-.91,13,0,35.53,0,47.5.91V771c-14.86-1.44-19.16-11.05-19.16-18.79,0-6.21,2.35-9.62,4.77-13L1017.85,627l83.55,122.8c3.78,5.32,3.78,6.75,3.78,8.19,0,3.78-4.3,12-21.12,13v11.6c15.38-.91,38.87-.91,54.69-.91,12.44,0,36.44,0,48.1.91V771c-30.76,0-35.61-2.35-45.15-15.9Z"
      fill="var(--primary-foreground)"
      transform="translate(-12.65 -12.46)"
    />
  </svg>
);

const AppToolbar = memo(function AppToolbar() {
  const navigate = useNavigate();
  const { t } = useTranslation("components");
  const handleNavigate = useCallback(() => navigate("/workspace"), [navigate]);

  return (
    <div className="fixed top-4 right-4 z-50 flex items-center gap-1">
      <ThemeSwitcher />
      <ThemeToggle />
      <Tooltip>
        <TooltipTrigger
          render={
            <Button variant="ghost" size="icon" onClick={handleNavigate} />
          }
        >
          <Archive className="size-5" />
          <span className="sr-only">{t("workspace")}</span>
        </TooltipTrigger>
        <TooltipContent>{t("workspace")}</TooltipContent>
      </Tooltip>
      <SettingsDialog />
    </div>
  );
});

export default function App() {
  useFavicon();

  return (
    <TooltipProvider>
      <BrowserRouter>
        {bgSvg}
        <AppToolbar />
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
            <Route
              path="/paragraph-translate"
              element={<ParagraphTranslatePage />}
            />
            <Route path="/code-review" element={<CodeReviewPage />} />
            <Route path="/format-convert" element={<FormatConvertPage />} />
            <Route path="/git-diff" element={<GitDiffPage />} />
            <Route
              path="/terminology-check"
              element={<TerminologyCheckPage />}
            />
            <Route path="/writing-chat" element={<WritingChatPage />} />
            <Route path="/bib-manager" element={<BibManagerPage />} />
          </Routes>
        </Suspense>
      </BrowserRouter>
    </TooltipProvider>
  );
}
