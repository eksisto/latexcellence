/**
 * EditorToolbar —— 编辑器工具栏（editor-plan §4 / §8）。
 *
 * 按钮：文件名与脏标记 · 后端撤销/重做 · 命令面板 · 编译 · 保存 · 编辑器设置。
 *
 * 撤销体系分两层（方案 §8）：缓冲区内的未保存修改由 CM6 自身 history 负责
 * （LaTeXEditor 内置），本栏的撤销/重做复用既有后端端点语义（已保存层，
 * 与 AI 改动共享同一套历史）：
 *   - 缓冲区有脏改动时禁用——undo 会改写磁盘，随后重拉内容会静默丢弃本地编辑；
 *   - undo/redo 成功后重拉文件内容刷新缓冲区（决策 Q11 统一记账语义）；
 *   - viewer 只读：保存与后端撤销一并禁用（编译为只读操作仍可用）。
 *
 * 编译按钮（阶段四 / 决策 Q18）：有脏改动时先自动保存（含乐观锁检查），
 * 失败中断编译进入既有冲突流程；编译执行与结果展示由 EditorPage 承担。
 */

import { useState } from "react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import Play from "lucide-react/dist/esm/icons/play.mjs";
import Save from "lucide-react/dist/esm/icons/save.mjs";
import Settings2 from "lucide-react/dist/esm/icons/settings-2.mjs";
import SquareTerminal from "lucide-react/dist/esm/icons/square-terminal.mjs";
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
 * @param {() => void} [props.onOpenPalette] 唤起命令面板
 * @param {boolean} [props.compileEnabled=true] 部署级 compile 功能开关
 * @param {boolean} [props.compiling=false] 编译请求进行中
 * @param {() => void} [props.onCompile]    编译入口（脏改动先自动保存）
 */
export default function EditorToolbar({
  filePath,
  saving,
  readOnly = false,
  onSave,
  onReloadBuffer,
  onOpenPalette,
  compileEnabled = true,
  compiling = false,
  onCompile,
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
  // viewer 可编译（只读操作）；无文件时不可编译；保存/编译互斥防并发写盘
  const compileDisabled = !filePath || compiling || (saving && dirty);

  return (
    // pr-9（36px）：设置按钮（size-icon，32px 盒）右缘落在视口-52px，
    // 图标中心随之落在「距右缘 68px」，与收起态书脊的居中图标同轴
    // （书脊 inset-1 后盒宽 32px，居中图标中心 = 视口-68px）
    <div className="flex flex-wrap items-center gap-2 pr-9">
      {/* 与页头「编辑器」标题左对齐（PageHeader 返回按钮 size-8 +
          gap-4 = 48px 缩进），路径与标题共用同一条纵向轴线 */}
      <span className="min-w-0 truncate pl-12 font-mono text-sm text-muted-foreground">
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

        <Tooltip>
          <TooltipTrigger
            render={
              <Button
                variant="outline"
                size="icon"
                onClick={onOpenPalette}
                disabled={!filePath}
              />
            }
          >
            <SquareTerminal />
            <span className="sr-only">
              {t("components:editor.commandPalette")}
            </span>
          </TooltipTrigger>
          <TooltipContent>
            {t("components:editor.commandPalette")}
          </TooltipContent>
        </Tooltip>

        {compileEnabled && (
          <Tooltip>
            <TooltipTrigger
              render={
                <Button
                  variant="outline"
                  size="sm"
                  onClick={onCompile}
                  disabled={compileDisabled}
                />
              }
            >
              {compiling ? <Loader2 className="animate-spin" /> : <Play />}
              {compiling
                ? t("components:editor.compiling")
                : t("components:editor.compile")}
            </TooltipTrigger>
            <TooltipContent>
              {t("components:editor.compileHint")}
            </TooltipContent>
          </Tooltip>
        )}

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
