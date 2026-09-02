import { useRef, useCallback, useState } from "react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import CompilePreviewPanel from "@/components/editor/CompilePreviewPanel";
import AssistantPanel from "@/components/editor/AssistantPanel";
import { cn } from "@/lib/utils";

import Bot from "lucide-react/dist/esm/icons/bot.mjs";
import PanelRightClose from "lucide-react/dist/esm/icons/panel-right-close.mjs";
import PanelRightOpen from "lucide-react/dist/esm/icons/panel-right-open.mjs";

/**
 * 右栏组装：编译与预览 ↔ AI 助手 Tab 切换，可折叠。
 * 面板折叠/Tab 状态由父级持有（handleCompile 需要直接 setPanelTab + 展开），
 * 本组件仅负责折叠/展开的焦点交接与动画编排。
 *
 * 动效编排（reveal 曲线 cubic-bezier(0.22,1,0.36,1)，与 .reveal 同源）：
 * ① 宽度壳层 320ms → ② 内容淡出左滑（展开时 +60ms 跟随入场）→
 * ③ 收起态「书脊」+150ms 压轴浮现。书脊以竖排文字标注当前隐藏
 * 的面板名（装订学术文档的书脊隐喻），整条可点恢复展开。
 * 两态常挂载，隐藏态加 inert 防止键盘焦点落入被裁切内容；折叠/
 * 展开时焦点在头部按钮与书脊之间交接。motion-reduce 全部直切。
 */
export default function EditorRightPanel({
  assistantEnabled,
  panelCollapsed,
  onCollapse,
  panelTab,
  onTabChange,
  compiling,
  compileResult,
  compileTarget,
  pdfUrl,
  previewFullscreen,
  onEnterFullscreen,
  onJumpToError,
  readOnly,
  getEditorContext,
  onApplySnippet,
  onApplyDiffText,
}) {
  const { t } = useTranslation(["pages", "components"]);

  const spineBtnRef = useRef(null);
  const panelCollapseRef = useRef(null);

  // 助手 Tab 首次激活后才挂载 AssistantPanel（此后保持挂载不丢草稿）：
  // 其静态导入链含 react-markdown + KaTeX 全家桶，进入 /editor 即挂载
  // 会把这些重型依赖一并急切加载，而多数会话并不使用助手
  const [assistantVisited, setAssistantVisited] = useState(false);
  const handleTabChange = useCallback(
    (value) => {
      if (value === "assistant") setAssistantVisited(true);
      onTabChange(value);
    },
    [onTabChange],
  );

  const collapsePanel = useCallback(() => {
    onCollapse(true);
    requestAnimationFrame(() => spineBtnRef.current?.focus());
  }, [onCollapse]);

  const expandPanelFromSpine = useCallback(() => {
    onCollapse(false);
    requestAnimationFrame(() => panelCollapseRef.current?.focus());
  }, [onCollapse]);

  return (
    <>
      <aside
        className={cn(
          "relative hidden xl:flex shrink-0 min-h-0 overflow-hidden",
          "mr-8",
          "transition-[width] duration-[320ms] ease-[cubic-bezier(0.22,1,0.36,1)] motion-reduce:transition-none",
          panelCollapsed ? "w-10" : "w-80",
        )}
      >
        <div
          inert={panelCollapsed || undefined}
          className={cn(
            "flex min-h-0 w-80 shrink-0 flex-col gap-2 p-1",
            "transition-[transform,opacity] duration-[320ms] ease-[cubic-bezier(0.22,1,0.36,1)] motion-reduce:transition-none",
            panelCollapsed
              ? "-translate-x-4 opacity-0 delay-0"
              : "translate-x-0 opacity-100 delay-[60ms]",
          )}
        >
          {assistantEnabled ? (
            <Tabs
              value={panelTab}
              onValueChange={handleTabChange}
              className="flex h-full min-h-0 flex-col gap-2"
            >
              <div className="flex items-center justify-between gap-1">
                <TabsList>
                  <TabsTrigger value="compile">
                    {t("components:editor.tabCompile")}
                  </TabsTrigger>
                  <TabsTrigger value="assistant">
                    <Bot className="size-3.5" />
                    {t("components:editor.tabAssistant")}
                  </TabsTrigger>
                </TabsList>
                <Button
                  ref={panelCollapseRef}
                  variant="ghost"
                  size="icon"
                  onClick={collapsePanel}
                  title={t("components:editor.collapsePanel")}
                >
                  <PanelRightClose />
                  <span className="sr-only">
                    {t("components:editor.collapsePanel")}
                  </span>
                </Button>
              </div>
              {/* 编译 Tab 常挂载（Base UI Panel 默认卸载非活动页）：
                  保留编译结果与预览 DOM，切换不重渲染 */}
              <TabsContent
                value="compile"
                keepMounted
                className="min-h-0 flex-1"
              >
                <CompilePreviewPanel
                  compiling={compiling}
                  compileResult={compileResult}
                  compileTarget={compileTarget}
                  pdfUrl={pdfUrl}
                  fullscreen={previewFullscreen}
                  onEnterFullscreen={onEnterFullscreen}
                  onJumpToError={onJumpToError}
                />
              </TabsContent>
              {/* 首次访问后 keepMounted：切换不丢助手输入草稿与卡片展开态；
                  会话消息本身在 Zustand store，本就不受切换影响 */}
              <TabsContent
                value="assistant"
                keepMounted={assistantVisited}
                className="min-h-0 flex-1"
              >
                {assistantVisited && (
                  <AssistantPanel
                    readOnly={readOnly}
                    getEditorContext={getEditorContext}
                    onApplySnippet={onApplySnippet}
                    onApplyDiffText={onApplyDiffText}
                    pollSelection={panelTab === "assistant" && !panelCollapsed}
                  />
                )}
              </TabsContent>
            </Tabs>
          ) : (
            <>
              <div className="flex items-center justify-end">
                <Button
                  ref={panelCollapseRef}
                  variant="ghost"
                  size="icon"
                  onClick={collapsePanel}
                  title={t("components:editor.collapsePanel")}
                >
                  <PanelRightClose />
                  <span className="sr-only">
                    {t("components:editor.collapsePanel")}
                  </span>
                </Button>
              </div>
              <div className="min-h-0 flex-1">
                <CompilePreviewPanel
                  compiling={compiling}
                  compileResult={compileResult}
                  compileTarget={compileTarget}
                  pdfUrl={pdfUrl}
                  fullscreen={previewFullscreen}
                  onEnterFullscreen={onEnterFullscreen}
                  onJumpToError={onJumpToError}
                />
              </div>
            </>
          )}
        </div>

        {/* 收起态「书脊」：整条可点展开。卡片同源外观（rounded-xl +
            foreground/10 描边），竖排文字标注当前隐藏面板名；展开时
            左移淡出让位（+0ms 先退场），收起时 +150ms 压轴浮现。
            inset-1 内缩 4px：描边向外扩散 1px，贴边会被 overflow
            裁掉直线段只剩四角圆弧 */}
        <button
          type="button"
          ref={spineBtnRef}
          inert={!panelCollapsed || undefined}
          onClick={expandPanelFromSpine}
          aria-label={t("components:editor.expandPanel")}
          title={t("components:editor.expandPanel")}
          className={cn(
            "absolute inset-1 flex cursor-pointer flex-col items-center gap-4 rounded-xl",
            "bg-card py-3 text-muted-foreground ring-1 ring-foreground/10 outline-none select-none",
            "hover:bg-muted/60 hover:text-foreground dark:hover:bg-input/30",
            "focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50",
            "transition-[transform,opacity,background-color] duration-200 ease-out motion-reduce:transition-none",
            panelCollapsed
              ? "translate-x-0 opacity-100 delay-150"
              : "pointer-events-none -translate-x-2 opacity-0 delay-0",
          )}
        >
          <PanelRightOpen className="size-4 shrink-0 opacity-70" />
          {/* 竖排书脊题名：正文楷体沿竖轴排布，呼应装订文档的书脊 */}
          <span className="[writing-mode:vertical-rl] text-xs leading-none tracking-[0.3em]">
            {assistantEnabled && panelTab === "assistant"
              ? t("components:editor.tabAssistant")
              : t("components:editor.tabCompile")}
          </span>
        </button>
      </aside>
    </>
  );
}
