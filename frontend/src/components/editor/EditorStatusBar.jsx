/**
 * EditorStatusBar —— 编辑器底部状态栏（editor-productivity-plan 阶段八，
 * 借鉴 Notepad++ 状态栏：光标行列 / 选区长度 / 全文行数与字数）。
 *
 * 统计快照经 statsHub（lib/editor/stats.js 的发布-订阅）流入，组件用
 * useSyncExternalStore 订阅——键入与光标移动只重渲染状态栏自身，页面
 * 其余部分（A2 按键渲染解耦）不受影响。hub 尚未发布快照时展示零态。
 */

import { useSyncExternalStore } from "react";
import { useTranslation } from "react-i18next";

export default function EditorStatusBar({ hub }) {
  const { t } = useTranslation("components");
  const stats = useSyncExternalStore(hub.subscribe, hub.getSnapshot);
  const selectedChars = stats?.selectedChars ?? 0;

  return (
    <div
      className="flex h-7 shrink-0 items-center gap-4 rounded-lg border border-border bg-muted/40 px-3 text-xs text-muted-foreground"
      data-testid="editor-status-bar"
    >
      <span>
        {t("components:editorStatusBar.cursor", {
          line: stats?.line ?? 1,
          col: stats?.col ?? 1,
        })}
      </span>
      {selectedChars > 0 && (
        <span>
          {t("components:editorStatusBar.selected", { count: selectedChars })}
        </span>
      )}
      <span className="ml-auto">
        {t("components:editorStatusBar.docStats", {
          lines: stats?.lines ?? 1,
          words: stats?.words ?? 0,
        })}
      </span>
    </div>
  );
}
