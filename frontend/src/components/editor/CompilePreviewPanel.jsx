import { useTranslation } from "react-i18next";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import EmptyState from "@/components/EmptyState";
import PDFPreview from "@/components/PDFPreview";

import Check from "lucide-react/dist/esm/icons/check.mjs";
import CircleX from "lucide-react/dist/esm/icons/circle-x.mjs";
import Expand from "lucide-react/dist/esm/icons/expand.mjs";
import Play from "lucide-react/dist/esm/icons/play.mjs";

import { translateError } from "@/utils/errorMessages";

/**
 * 编译与预览面板：编译状态徽标、错误翻译与修复建议摘要、
 * 原始输出折叠块，成功时复用 PDFPreview 预览。
 * 与 GitDiffPage 同源：两侧编译/渲染结果共用同一组件。
 */
export default function CompilePreviewPanel({
  compiling,
  compileResult,
  pdfUrl,
  fullscreen,
  onEnterFullscreen,
}) {
  const { t } = useTranslation(["pages", "components"]);
  return (
    <Card className="flex h-full min-h-0 flex-col overflow-hidden">
      <CardHeader className="pb-2">
        <div className="flex items-center justify-between gap-2">
          <CardTitle className="text-sm">
            {t("components:editor.panelCompileTitle")}
          </CardTitle>
          {compileResult && (
            <span
              className={`inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-xs font-medium ${
                compileResult.success
                  ? "bg-secondary text-secondary-foreground"
                  : "bg-destructive/10 text-destructive"
              }`}
            >
              {compileResult.success ? (
                <>
                  <Check className="size-3" />
                  {t("pages:workspace.compileSuccess")}
                </>
              ) : (
                <>
                  <CircleX className="size-3" />
                  {t("pages:workspace.compileFailed")}
                </>
              )}
            </span>
          )}
        </div>
      </CardHeader>
      <CardContent className="flex min-h-0 flex-1 flex-col gap-2 overflow-y-auto p-3 pt-0">
        {compiling && (
          <p className="text-xs text-muted-foreground">
            {t("components:editor.compiling")}
          </p>
        )}
        {!compiling && !compileResult && (
          <EmptyState
            icon={Play}
            title={t("components:editor.panelEmptyTitle")}
            description={t("components:editor.panelEmptyDescription")}
            className="py-8"
          />
        )}
        {compileResult && (
          <>
            {compileResult.explanation && (
              <p className="text-xs leading-relaxed text-muted-foreground">
                {translateError({
                  error: compileResult.error_code,
                  message: compileResult.explanation,
                })}
              </p>
            )}
            {Array.isArray(compileResult.fixes) &&
              compileResult.fixes.length > 0 && (
                <ul className="space-y-1 text-xs">
                  {compileResult.fixes.map((fix, i) => (
                    <li key={i} className="rounded border bg-background p-1.5">
                      {typeof fix === "string"
                        ? fix
                        : fix.suggestion || fix.description || ""}
                    </li>
                  ))}
                </ul>
              )}
            <details className="text-xs">
              <summary className="cursor-pointer text-muted-foreground">
                {t("components:editor.compileOutput")}
              </summary>
              <pre className="mt-1 max-h-40 overflow-auto rounded bg-muted/60 p-2 font-mono text-[11px] whitespace-pre-wrap break-all">
                {(compileResult.stdout || "") + (compileResult.stderr || "")}
              </pre>
            </details>
          </>
        )}
        {/* 成功后 PDF 预览占满剩余空间；全屏时卸载内联预览（避免双 iframe），
            左下角悬浮「全屏查看」按钮与 GitDiff 页同款 */}
        {compileResult?.success && pdfUrl && !fullscreen && (
          <div className="relative min-h-32 flex-1">
            <PDFPreview url={pdfUrl} />
            <Button
              variant="outline"
              size="sm"
              className="absolute bottom-2 left-2 z-10 bg-background/80 backdrop-blur"
              onClick={onEnterFullscreen}
            >
              <Expand />
              {t("components:editor.enterFullscreen")}
            </Button>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
