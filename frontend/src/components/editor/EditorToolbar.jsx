/**
 * EditorToolbar —— 编辑器工具栏（editor-plan §4 / §8 撤销体系）。
 *
 * 按钮：返回工作台 · 文件名与脏标记 · 后端撤销/重做 · 保存 · 编辑器设置。
 *
 * 撤销体系分两层（方案 §8）：缓冲区内的未保存修改由 CM6 自身 history 负责
 * （LaTeXEditor 内置），本栏的撤销/重做复用既有后端端点语义（已保存层，
 * 与 AI 改动共享同一套历史）：
 *   - 缓冲区有脏改动时禁用——undo 会改写磁盘，随后重拉内容会静默丢弃本地编辑；
 *   - undo/redo 成功后重拉文件内容刷新缓冲区（决策 Q11 统一记账语义）；
 *   - viewer 只读：保存与后端撤销一并禁用。
 *
 * 编译按钮的自动保存联动属阶段四，本阶段不放置编译入口。
 */

import { useState } from "react";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import ArrowLeft from "lucide-react/dist/esm/icons/arrow-left.mjs";
import Save from "lucide-react/dist/esm/icons/save.mjs";
import Settings2 from "lucide-react/dist/esm/icons/settings-2.mjs";
import Undo2 from "lucide-react/dist/esm/icons/undo-2.mjs";
import Redo2 from "lucide-react/dist/esm/icons/redo-2.mjs";
import Loader2 from "lucide-react/dist/esm/icons/loader-circle.mjs";

import useStore from "@/store/useStore";
import useEditorStore from "@/store/editorStore";
import useCanWrite from "@/hooks/useCanWrite";
import useUndoRedo from "@/hooks/useUndoRedo";
import EditorSettingsDialog from "@/components/editor/EditorSettingsDialog";

/**
 * @param {object} props
 * @param {string|null} props.filePath      当前打开的文件路径
 * @param {boolean} props.saving            保存请求进行中
 * @param {boolean} [props.readOnly=false]  viewer 只读
 * @param {() => void|Promise<void>} props.onSave
 * @param {() => void|Promise<void>} props.onReloadBuffer
 *   后端撤销/重做落盘后重拉文件内容刷新缓冲区
 */
export default function EditorToolbar({
  filePath,
  saving,
  readOnly = false,
  onSave,
  onReloadBuffer,
}) {
  const { t } = useTranslation("components");
  const workspaceId = useStore((s) => s.workspaceId);
  const dirty = useEditorStore((s) => s.dirty);
  const canWrite = useCanWrite();

  const [settingsOpen, setSettingsOpen] = useState(false);

  // 复用既有 undo-status 轮询与 undo/redo 端点语义；成功后经回调重拉缓冲区
  const {
    canUndo,
    canRedo,
    busy: historyBusy,
    handleUndo,
    handleRedo,
  } = useUndoRedo(readOnly ? null : filePath, workspaceId, onReloadBuffer);

  const saveDisabled = readOnly || !canWrite || !dirty || saving;
  const historyDisabled = readOnly || !canWrite || dirty;

  return (
    <div className="flex flex-wrap items-center gap-2">
      <Button
        variant="ghost"
        size="sm"
        nativeButton={false}
        render={<Link to="/workspace" />}
      >
        <ArrowLeft />
        {t("components:editor.backToWorkspace")}
      </Button>

      <span className="ml-1 min-w-0 truncate font-mono text-sm text-muted-foreground">
        {filePath || t("components:editor.noFile")}
        {dirty && (
          <span
            className="ml-1.5 inline-block size-2 rounded-full bg-primary align-middle"
            title={t("components:editor.unsavedDot")}
          />
        )}
      </span>

      <div className="ml-auto flex items-center gap-1">
        <Tooltip>
          <TooltipTrigger
            render={
              <Button
                variant="outline"
                size="icon"
                onClick={handleUndo}
                disabled={!canUndo || historyBusy || historyDisabled}
              />
            }
          >
            <Undo2 />
            <span className="sr-only">
              {t("components:editor.backendUndo")}
            </span>
          </TooltipTrigger>
          <TooltipContent>
            {historyDisabled && dirty
              ? t("components:editor.historyBlockedByDirty")
              : t("components:editor.backendUndo")}
          </TooltipContent>
        </Tooltip>
        <Tooltip>
          <TooltipTrigger
            render={
              <Button
                variant="outline"
                size="icon"
                onClick={handleRedo}
                disabled={!canRedo || historyBusy || historyDisabled}
              />
            }
          >
            <Redo2 />
            <span className="sr-only">
              {t("components:editor.backendRedo")}
            </span>
          </TooltipTrigger>
          <TooltipContent>
            {historyDisabled && dirty
              ? t("components:editor.historyBlockedByDirty")
              : t("components:editor.backendRedo")}
          </TooltipContent>
        </Tooltip>

        <Button
          size="sm"
          onClick={onSave}
          disabled={saveDisabled}
          className="ml-1"
        >
          {saving ? (
            <>
              <Loader2 className="animate-spin" />
              {t("components:editor.saving")}
            </>
          ) : (
            <>
              <Save />
              {t("components:editor.save")}
            </>
          )}
        </Button>

        <Tooltip>
          <TooltipTrigger
            render={
              <Button
                variant="outline"
                size="icon"
                onClick={() => setSettingsOpen(true)}
              />
            }
          >
            <Settings2 />
            <span className="sr-only">
              {t("components:editorSettings.title")}
            </span>
          </TooltipTrigger>
          <TooltipContent>
            {t("components:editorSettings.title")}
          </TooltipContent>
        </Tooltip>
      </div>

      <EditorSettingsDialog
        open={settingsOpen}
        onOpenChange={setSettingsOpen}
      />
    </div>
  );
}
