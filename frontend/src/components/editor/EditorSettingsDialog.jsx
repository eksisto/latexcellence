/**
 * EditorSettingsDialog —— 编辑器偏好设置（editor-design §9 / §11）。
 *
 * 开关：软换行、字号即时生效；自动保存（默认关，决策 Q6）在缓冲区停止
 * 编辑后延迟落盘（行为联动属阶段四，经 save-file 单一路径）；实时语法
 * 诊断（editor v3 阶段三 / 决策 Q9）切换后经扩展重配置即时生效。
 *
 * 自定义片段（editor v3 阶段二 / 决策 Q7、Q8）：列表 + 增删改表单
 * （命令名 / 插入内容 / 中文名），插入内容带实时预览（占位符解析后的
 * 实际落盘文本，网格模板按派生默认行列展开）；条目经
 * setCustomSnippets 写回（sanitizePreferences 条目级清洗兜底），并跟随
 * 既有偏好导入导出双通道分发（serializePreferences / parsePreferencesImport
 * v2 载荷，不另开通道）。
 *
 * 偏好导入/导出（§11 / 决策 Q19-Q21）：导出 pretty-print 的
 * kind/version/prefs 结构；导入支持选文件或粘贴 JSON 双通道，按
 * version + 字段白名单严格校验（校验逻辑见 lib/editor/preferences.js
 * 纯模块），非法字段静默丢弃，合法字段整体覆盖当前切片，toast 反馈结果。
 *
 * 注：计划形态为 Popover 弹层，仓库 ui 基座未含 popover 原语，按 shadcn/ui
 * 优先原则改用现有 Dialog 实现，交互语义等价。
 */

import { useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import Download from "lucide-react/dist/esm/icons/download.mjs";
import FileUp from "lucide-react/dist/esm/icons/file-up.mjs";
import ClipboardPaste from "lucide-react/dist/esm/icons/clipboard-paste.mjs";
import Pencil from "lucide-react/dist/esm/icons/pencil.mjs";
import Plus from "lucide-react/dist/esm/icons/plus.mjs";
import Trash2 from "lucide-react/dist/esm/icons/trash-2.mjs";

import useEditorStore from "@/store/editorStore";
import { useShallow } from "zustand/react/shallow";
import {
  CUSTOM_SNIPPET_INSERT_MAX,
  CUSTOM_SNIPPETS_LIMIT,
  parsePreferencesImport,
  serializePreferences,
} from "@/lib/editor/preferences";
import { normalizeCustomSnippet } from "@/lib/editor/customCommands";
import { LATEX_COMMANDS } from "@/data/latexCommands/index.js";
import { expandEntryInsert, renderSnippet } from "@/lib/editor/snippetInsert";

const FONT_SIZE_OPTIONS = [12, 13, 14, 16, 18, 20];
const EXPORT_FILENAME = "latexcellence-editor-preferences.json";

/** 命令名约束（与命令数据库 / 偏好清洗同规则；存储前 sanitize 已裁剪） */
const CUSTOM_NAME_RE = /^[A-Za-z][A-Za-z0-9*]*$/;

/** 编辑表单态：index = -1 表示新增，否则为待改条目下标 */
const EMPTY_DRAFT = { index: -1, name: "", zh: "", insert: "" };

export default function EditorSettingsDialog({ open, onOpenChange }) {
  const { t } = useTranslation("components");

  const {
    softWrap,
    autoSave,
    autoCloseEnv,
    formulaHover,
    selectionMatches,
    liveDiagnostics,
    fontSize,
    customSnippets,
    setSoftWrap,
    setAutoSave,
    setAutoCloseEnv,
    setFormulaHover,
    setSelectionMatches,
    setLiveDiagnostics,
    setFontSize,
    setCustomSnippets,
    applyPreferences,
  } = useEditorStore(
    useShallow((s) => ({
      softWrap: s.softWrap,
      autoSave: s.autoSave,
      autoCloseEnv: s.autoCloseEnv,
      formulaHover: s.formulaHover,
      selectionMatches: s.selectionMatches,
      liveDiagnostics: s.liveDiagnostics,
      fontSize: s.fontSize,
      customSnippets: s.customSnippets,
      setSoftWrap: s.setSoftWrap,
      setAutoSave: s.setAutoSave,
      setAutoCloseEnv: s.setAutoCloseEnv,
      setFormulaHover: s.setFormulaHover,
      setSelectionMatches: s.setSelectionMatches,
      setLiveDiagnostics: s.setLiveDiagnostics,
      setFontSize: s.setFontSize,
      setCustomSnippets: s.setCustomSnippets,
      applyPreferences: s.applyPreferences,
    })),
  );

  const [pasteOpen, setPasteOpen] = useState(false);
  const [pasteText, setPasteText] = useState("");
  const [draft, setDraft] = useState(null);
  const fileInputRef = useRef(null);

  const handleExport = () => {
    const prefs = useEditorStore.getState();
    const blob = new Blob(
      [
        serializePreferences({
          autoSave: prefs.autoSave,
          softWrap: prefs.softWrap,
          autoCloseEnv: prefs.autoCloseEnv,
          formulaHover: prefs.formulaHover,
          selectionMatches: prefs.selectionMatches,
          liveDiagnostics: prefs.liveDiagnostics,
          fontSize: prefs.fontSize,
          customSnippets: prefs.customSnippets,
        }),
      ],
      { type: "application/json" },
    );
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = EXPORT_FILENAME;
    anchor.click();
    URL.revokeObjectURL(url);
  };

  /** 导入统一出口：整体覆盖切片并反馈（合法字段生效，非法静默丢弃） */
  const handleImportText = (text) => {
    const result = parsePreferencesImport(text);
    if (!result.ok) {
      toast.error(
        t(`editorSettings.import_${result.reason}`, {
          defaultValue: t("editorSettings.importFailed"),
        }),
      );
      return;
    }
    applyPreferences(result.prefs);
    toast.success(t("editorSettings.importSuccess"));
    setPasteOpen(false);
    setPasteText("");
  };

  const handleImportFile = async (file) => {
    if (!file) return;
    try {
      const text = await file.text();
      handleImportText(text);
    } catch (_) {
      toast.error(t("editorSettings.import_invalid_json"));
    }
  };

  /* ── 自定义片段：表单校验 / 实时预览 / 增删改 ─────────────────────── */

  const draftName = draft?.name.trim() ?? "";

  /** 名称冲突：与内建命令同名（合并层会丢弃），或与其他自定义片段重名 */
  const draftNameTaken =
    CUSTOM_NAME_RE.test(draftName) &&
    (LATEX_COMMANDS.some((entry) => entry.name === draftName) ||
      customSnippets.some(
        (entry, i) => entry.name === draftName && i !== draft.index,
      ));

  const draftError = useMemo(() => {
    if (!draft) return null;
    if (!CUSTOM_NAME_RE.test(draftName)) return "customErrName";
    if (draftNameTaken) return "customErrTaken";
    if (draft.insert.length === 0) return "customErrInsertEmpty";
    if (draft.insert.length > CUSTOM_SNIPPET_INSERT_MAX) {
      return "customErrInsertTooLong";
    }
    return null;
  }, [draft, draftName, draftNameTaken]);

  /** 实时预览：占位符解析后的实际插入文本（网格模板按派生默认行列展开） */
  const draftPreview = useMemo(() => {
    if (!draft) return "";
    const entry = normalizeCustomSnippet({
      name: draftName,
      insert: draft.insert,
      zh: draft.zh,
    });
    return renderSnippet(expandEntryInsert(entry)).text;
  }, [draft, draftName]);

  /** 保存（新增 / 编辑共用）：写回 store（setCustomSnippets 内再做条目级清洗） */
  const saveDraft = () => {
    if (!draft || draftError) return;
    const entry = {
      name: draftName,
      insert: draft.insert,
      zh: draft.zh.trim(),
      category: "custom",
    };
    setCustomSnippets((prev) => {
      if (draft.index >= 0) {
        return prev.map((item, i) => (i === draft.index ? entry : item));
      }
      return [...prev, entry];
    });
    setDraft(null);
  };

  const removeSnippet = (index) => {
    setCustomSnippets((prev) => prev.filter((_, i) => i !== index));
    setDraft((cur) => (cur?.index === index ? null : cur));
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{t("components:editorSettings.title")}</DialogTitle>
          <DialogDescription>
            {t("components:editorSettings.description")}
          </DialogDescription>
        </DialogHeader>

        <div className="flex flex-col gap-4">
          <div className="flex items-center justify-between gap-4">
            <Label htmlFor="editor-softwrap" className="text-sm">
              {t("components:editorSettings.softWrap")}
            </Label>
            <Switch
              id="editor-softwrap"
              checked={softWrap}
              onCheckedChange={(checked) => setSoftWrap(checked === true)}
            />
          </div>

          <div className="flex items-center justify-between gap-4">
            <Label htmlFor="editor-autosave" className="text-sm">
              {t("components:editorSettings.autoSave")}
            </Label>
            <Switch
              id="editor-autosave"
              checked={autoSave}
              onCheckedChange={(checked) => setAutoSave(checked === true)}
            />
          </div>
          <p className="-mt-2 text-xs text-muted-foreground">
            {t("components:editorSettings.autoSaveHint")}
          </p>

          <div className="flex items-center justify-between gap-4">
            <Label htmlFor="editor-autoclose-env" className="text-sm">
              {t("components:editorSettings.autoCloseEnv")}
            </Label>
            <Switch
              id="editor-autoclose-env"
              checked={autoCloseEnv}
              onCheckedChange={(checked) => setAutoCloseEnv(checked === true)}
            />
          </div>
          <p className="-mt-2 text-xs text-muted-foreground">
            {t("components:editorSettings.autoCloseEnvHint")}
          </p>

          <div className="flex items-center justify-between gap-4">
            <Label htmlFor="editor-formula-hover" className="text-sm">
              {t("components:editorSettings.formulaHover")}
            </Label>
            <Switch
              id="editor-formula-hover"
              checked={formulaHover}
              onCheckedChange={(checked) => setFormulaHover(checked === true)}
            />
          </div>
          <p className="-mt-2 text-xs text-muted-foreground">
            {t("components:editorSettings.formulaHoverHint")}
          </p>

          <div className="flex items-center justify-between gap-4">
            <Label htmlFor="editor-selection-matches" className="text-sm">
              {t("components:editorSettings.selectionMatches")}
            </Label>
            <Switch
              id="editor-selection-matches"
              checked={selectionMatches}
              onCheckedChange={(checked) =>
                setSelectionMatches(checked === true)
              }
            />
          </div>
          <p className="-mt-2 text-xs text-muted-foreground">
            {t("components:editorSettings.selectionMatchesHint")}
          </p>

          {/* 实时语法诊断（editor v3 阶段三 / 决策 Q9）：CM6 lint 内联波浪线 */}
          <div className="flex items-center justify-between gap-4">
            <Label htmlFor="editor-live-diagnostics" className="text-sm">
              {t("components:editorSettings.liveDiagnostics")}
            </Label>
            <Switch
              id="editor-live-diagnostics"
              checked={liveDiagnostics}
              onCheckedChange={(checked) =>
                setLiveDiagnostics(checked === true)
              }
            />
          </div>
          <p className="-mt-2 text-xs text-muted-foreground">
            {t("components:editorSettings.liveDiagnosticsHint")}
          </p>

          <div className="flex items-center justify-between gap-4">
            <Label htmlFor="editor-font-size" className="text-sm">
              {t("components:editorSettings.fontSize")}
            </Label>
            <Select
              value={String(fontSize)}
              onValueChange={(v) => setFontSize(Number(v))}
            >
              <SelectTrigger id="editor-font-size" className="w-24">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {FONT_SIZE_OPTIONS.map((size) => (
                  <SelectItem key={size} value={String(size)}>
                    {size} px
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          {/* 自定义片段（editor v3 阶段二）：增删改表单 + 实时预览；跟随
              既有偏好导入导出通道分发，不另开通道 */}
          <div className="space-y-2 rounded-md border p-2.5">
            <div className="flex items-center justify-between gap-2">
              <Label className="text-xs text-muted-foreground">
                {t("components:editorSettings.customTitle")}
              </Label>
              <span className="text-xs text-muted-foreground">
                {t("components:editorSettings.customCount", {
                  current: customSnippets.length,
                  max: CUSTOM_SNIPPETS_LIMIT,
                })}
              </span>
            </div>

            {draft ? (
              <div className="space-y-2">
                <div className="grid grid-cols-2 gap-2">
                  <div className="space-y-1">
                    <Label
                      htmlFor="custom-snippet-name"
                      className="text-xs text-muted-foreground"
                    >
                      {t("components:editorSettings.customName")}
                    </Label>
                    <Input
                      id="custom-snippet-name"
                      value={draft.name}
                      onChange={(e) =>
                        setDraft({ ...draft, name: e.target.value })
                      }
                      placeholder="myenv"
                      className="h-8 font-mono text-xs"
                      autoComplete="off"
                    />
                  </div>
                  <div className="space-y-1">
                    <Label
                      htmlFor="custom-snippet-zh"
                      className="text-xs text-muted-foreground"
                    >
                      {t("components:editorSettings.customZh")}
                    </Label>
                    <Input
                      id="custom-snippet-zh"
                      value={draft.zh}
                      onChange={(e) =>
                        setDraft({ ...draft, zh: e.target.value })
                      }
                      placeholder={t(
                        "components:editorSettings.customZhPlaceholder",
                      )}
                      className="h-8 text-xs"
                      autoComplete="off"
                    />
                  </div>
                </div>
                <div className="space-y-1">
                  <Label
                    htmlFor="custom-snippet-insert"
                    className="text-xs text-muted-foreground"
                  >
                    {t("components:editorSettings.customInsert")}
                  </Label>
                  <Textarea
                    id="custom-snippet-insert"
                    value={draft.insert}
                    onChange={(e) =>
                      setDraft({ ...draft, insert: e.target.value })
                    }
                    placeholder={t(
                      "components:editorSettings.customInsertPlaceholder",
                    )}
                    className="min-h-16 font-mono text-xs"
                  />
                  <p className="text-xs text-muted-foreground">
                    {t("components:editorSettings.customInsertHint")}
                  </p>
                </div>
                {draftError && (
                  <p className="text-xs text-destructive">
                    {t(`components:editorSettings.${draftError}`)}
                  </p>
                )}
                <div className="space-y-1">
                  <Label className="text-xs text-muted-foreground">
                    {t("components:editorSettings.customPreview")}
                  </Label>
                  <pre className="max-h-24 overflow-y-auto rounded bg-muted px-2 py-1.5 font-mono text-xs break-all whitespace-pre-wrap">
                    {draftPreview || "—"}
                  </pre>
                </div>
                <div className="flex justify-end gap-2">
                  <Button
                    size="xs"
                    variant="ghost"
                    onClick={() => setDraft(null)}
                  >
                    {t("components:editorSettings.customCancel")}
                  </Button>
                  <Button size="xs" onClick={saveDraft} disabled={!!draftError}>
                    {t("components:editorSettings.customSave")}
                  </Button>
                </div>
              </div>
            ) : (
              <div className="space-y-1.5">
                {customSnippets.length === 0 ? (
                  <p className="text-xs text-muted-foreground">
                    {t("components:editorSettings.customEmpty")}
                  </p>
                ) : (
                  <div className="max-h-40 space-y-1 overflow-y-auto pr-1">
                    {customSnippets.map((snippet, index) => (
                      <div
                        key={snippet.name}
                        className="flex items-center gap-1.5 rounded border bg-background px-2 py-1"
                      >
                        <div className="min-w-0 flex-1">
                          <p className="truncate font-mono text-xs font-medium">
                            \{snippet.name}
                          </p>
                          <p
                            className="truncate text-xs text-muted-foreground"
                            title={snippet.insert}
                          >
                            {snippet.zh || snippet.insert.replace(/\s+/g, " ")}
                          </p>
                        </div>
                        <Button
                          size="icon-xs"
                          variant="ghost"
                          onClick={() =>
                            setDraft({
                              index,
                              name: snippet.name,
                              zh: snippet.zh,
                              insert: snippet.insert,
                            })
                          }
                          aria-label={t("components:editorSettings.customEdit")}
                        >
                          <Pencil />
                        </Button>
                        <Button
                          size="icon-xs"
                          variant="ghost"
                          onClick={() => removeSnippet(index)}
                          aria-label={t(
                            "components:editorSettings.customDelete",
                          )}
                        >
                          <Trash2 />
                        </Button>
                      </div>
                    ))}
                  </div>
                )}
                <Button
                  size="xs"
                  variant="outline"
                  onClick={() => setDraft({ ...EMPTY_DRAFT })}
                  disabled={customSnippets.length >= CUSTOM_SNIPPETS_LIMIT}
                >
                  <Plus />
                  {t("components:editorSettings.customAdd")}
                </Button>
              </div>
            )}
          </div>

          {/* 偏好导入/导出（§11）：入口在编辑器设置弹层，不放全局设置对话框 */}
          <div className="space-y-2 rounded-md border p-2.5 bg-muted/40">
            <Label className="text-xs text-muted-foreground">
              {t("components:editorSettings.transferTitle")}
            </Label>
            <div className="flex flex-wrap items-center gap-2">
              <Button size="xs" variant="outline" onClick={handleExport}>
                <Download />
                {t("components:editorSettings.export")}
              </Button>
              <Button
                size="xs"
                variant="outline"
                onClick={() => fileInputRef.current?.click()}
              >
                <FileUp />
                {t("components:editorSettings.importFile")}
              </Button>
              <Button
                size="xs"
                variant="ghost"
                onClick={() => setPasteOpen((v) => !v)}
              >
                <ClipboardPaste />
                {t("components:editorSettings.importPaste")}
              </Button>
              {/* 隐藏文件选择器：导入通道一（选文件） */}
              <input
                ref={fileInputRef}
                type="file"
                accept=".json,application/json"
                className="hidden"
                onChange={(e) => {
                  handleImportFile(e.target.files?.[0]);
                  e.target.value = "";
                }}
              />
            </div>
            {pasteOpen && (
              <div className="space-y-1.5">
                <Textarea
                  value={pasteText}
                  onChange={(e) => setPasteText(e.target.value)}
                  placeholder='{"kind": "...", "version": 2, "prefs": {...}}'
                  className="min-h-20 font-mono text-xs"
                />
                <Button
                  size="xs"
                  onClick={() => handleImportText(pasteText)}
                  disabled={!pasteText.trim()}
                >
                  {t("components:editorSettings.importApply")}
                </Button>
              </div>
            )}
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
