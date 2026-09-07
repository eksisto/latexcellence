import { useMemo } from "react";
import { useTranslation } from "react-i18next";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import EmptyState from "@/components/EmptyState";
import PDFPreview from "@/components/PDFPreview";

import Check from "lucide-react/dist/esm/icons/check.mjs";
import CircleX from "lucide-react/dist/esm/icons/circle-x.mjs";
import Expand from "lucide-react/dist/esm/icons/expand.mjs";
import Play from "lucide-react/dist/esm/icons/play.mjs";
import TriangleAlert from "lucide-react/dist/esm/icons/triangle-alert.mjs";

import { translateError } from "@/utils/errorMessages";
import { parseLogErrors } from "@/lib/editor/logFileStack";
import { cn } from "@/lib/utils";

/**
 * 编译与预览面板：编译状态徽标、错误翻译与修复建议摘要、
 * 可点击错误列表（阶段七 / Q10+Q19：日志文件栈解析 → 跨文件跳转）、
 * 原始输出折叠块，成功时复用 PDFPreview 预览。
 * 与 GitDiffPage 同源：两侧编译/渲染结果共用同一组件。
 */

/** 单个可点击错误条目：文件相对路径 + l.N + 消息摘要，低置信标角标 */
function ErrorListItem({ entry, onJump }) {
  const { t } = useTranslation("components");
  return (
    <li>
      <button
        type="button"
        onClick={() => onJump(entry)}
        title={entry.detail || entry.message}
        className={cn(
          "w-full rounded border bg-background p-1.5 text-left",
          "outline-none transition-colors select-none",
          "hover:bg-muted/70 focus-visible:bg-muted/70",
          "focus-visible:ring-2 focus-visible:ring-ring/50",
        )}
      >
        <span className="flex items-center gap-1.5 font-mono text-[11px] text-muted-foreground">
          <span className="min-w-0 truncate">{entry.file ?? "?"}</span>
          <span className="shrink-0">l.{entry.line}</span>
          {entry.lowConfidence && (
            <span
              title={t("editor.errorList.lowConfidence")}
              className="shrink-0 text-amber-500"
            >
              <TriangleAlert className="size-3" />
              <span className="sr-only">
                {t("editor.errorList.lowConfidence")}
              </span>
            </span>
          )}
        </span>
        <span className="mt-0.5 block truncate text-xs">
          {entry.message || entry.detail}
        </span>
      </button>
    </li>
  );
}

export default function CompilePreviewPanel({
  compiling,
  compileResult,
  pdfUrl,
  fullscreen,
  onEnterFullscreen,
  compileTarget = null,
  onJumpToError = null,
  // SyncTeX 正反向同步通道（editor v3 阶段一）：编辑器页注入，
  // GitDiff 页等未注入的使用点保持既有纯预览形态不变
  forwardTarget = null,
  onBackwardSync = null,
}) {
  const { t } = useTranslation(["pages", "components"]);
  // 错误列表（阶段七）：仅失败结果 + 注入了跳转回调时解析展示
  // （GitDiff 页同源组件未注入回调，保持既有形态不变）
  const errorEntries = useMemo(() => {
    if (!compileResult || compileResult.success || !onJumpToError) return [];
    return parseLogErrors(compileResult.stdout || "", compileTarget);
  }, [compileResult, compileTarget, onJumpToError]);
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
        {!compiling && !compileResult && !pdfUrl && (
          <EmptyState
            icon={Play}
            title={t("components:editor.panelEmptyTitle")}
            description={t("components:editor.panelEmptyDescription")}
            className="py-8"
          />
        )}
        {/* 刷新后无本次编译状态但已恢复既有产物：提示来源为历史编译 */}
        {pdfUrl && !compiling && !compileResult && (
          <p className="text-xs text-muted-foreground">
            {t("components:editor.panelRestoredHint")}
          </p>
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
            {!compileResult.success && onJumpToError && (
              <div className="space-y-1">
                <p className="text-xs font-medium text-foreground/80">
                  {t("components:editor.errorList.title")}
                </p>
                {errorEntries.length === 0 ? (
                  <p className="text-xs text-muted-foreground">
                    {t("components:editor.errorList.empty")}
                  </p>
                ) : (
                  <ul
                    className="space-y-1"
                    aria-label={t("components:editor.errorList.title")}
                  >
                    {errorEntries.map((entry, index) => (
                      <ErrorListItem
                        key={`${entry.file}-${entry.line}-${index}`}
                        entry={entry}
                        onJump={onJumpToError}
                      />
                    ))}
                  </ul>
                )}
              </div>
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
        {/* 成功后（或刷新恢复的历史产物）PDF 预览占满剩余空间；
            全屏时卸载内联预览（避免双实例），
            左下角悬浮「全屏查看」按钮与 GitDiff 页同款 */}
        {pdfUrl && !fullscreen && (!compileResult || compileResult.success) && (
          <div className="relative min-h-32 flex-1">
            <PDFPreview
              url={pdfUrl}
              forwardTarget={forwardTarget}
              onBackwardSync={onBackwardSync}
            />
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
