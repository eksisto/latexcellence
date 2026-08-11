import { useState, useCallback, useEffect, useRef, useMemo } from "react";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  CardAction,
} from "@/components/ui/card";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Badge } from "@/components/ui/badge";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import {
  FolderOpen,
  SpellCheck,
  Play,
  Loader2,
  Check,
  CircleX,
  ChevronDown,
  ChevronRight,
  FileDiff,
  Undo2,
  Redo2,
  FileTypeCorner,
} from "lucide-react";
import { api } from "@/api/index";
import { translateError } from "@/utils/errorMessages";
import useStore from "@/store/useStore";
import useEnabledFeatures from "@/hooks/useEnabledFeatures";
import useUndoRedo from "@/hooks/useUndoRedo";
import ProjectExplorer from "@/components/ProjectExplorer";
import PageHeader from "@/components/PageHeader";
import CodeBlock from "@/components/CodeBlock";
import ConfirmDialog from "@/components/ConfirmDialog";
import FolderPickerDialog from "@/components/FolderPickerDialog";
import PDFPreview from "@/components/PDFPreview";

function flattenTexFiles(nodes) {
  const result = [];
  for (const node of nodes) {
    if (!node.is_dir && node.name.endsWith(".tex")) {
      result.push(node);
    }
    if (node.children) {
      result.push(...flattenTexFiles(node.children));
    }
  }
  return result;
}

export default function WorkspacePage() {
  const { t } = useTranslation(["pages", "common"]);
  const projectPath = useStore((s) => s.projectPath);
  const workspaceId = useStore((s) => s.workspaceId);
  const rootPath = useStore((s) => s.rootPath);
  const fileTree = useStore((s) => s.fileTree);
  const activeFile = useStore((s) => s.activeFile);
  const mainFile = useStore((s) => s.mainFile);
  const setProjectPath = useStore((s) => s.setProjectPath);
  const setRootPath = useStore((s) => s.setRootPath);
  const setFileTree = useStore((s) => s.setFileTree);
  const setActiveFile = useStore((s) => s.setActiveFile);
  const setMainFile = useStore((s) => s.setMainFile);
  const clearProject = useStore((s) => s.clearProject);
  const enabledFeatures = useEnabledFeatures();
  const compileResult = useStore((s) => s.compileResult);
  const compileLoading = useStore((s) => s.compileLoading);
  const setCompileResult = useStore((s) => s.setCompileResult);
  const setCompileLoading = useStore((s) => s.setCompileLoading);
  const pdfUrl = useStore((s) => s.pdfUrl);
  const setPdfUrl = useStore((s) => s.setPdfUrl);

  const texFiles = useMemo(() => flattenTexFiles(fileTree), [fileTree]);

  const [openPath, setOpenPath] = useState("");
  const [paragraphs, setParagraphs] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [showRawOutput, setShowRawOutput] = useState(false);
  const [confirmCloseOpen, setConfirmCloseOpen] = useState(false);
  const [confirmUndoOpen, setConfirmUndoOpen] = useState(false);
  const [folderPickerOpen, setFolderPickerOpen] = useState(false);
  const restoredRef = useRef(false);

  const handleCloseProject = useCallback(() => {
    clearProject();
    setParagraphs([]);
    setPdfUrl(null);
    setConfirmCloseOpen(false);
    toast.success(t("pages:workspace.projectClosed"));
  }, [clearProject, setPdfUrl, t]);

  const doOpenProject = useCallback(
    async (path) => {
      setLoading(true);
      setError(null);
      try {
        const data = await api.project.open(path, workspaceId);
        setRootPath(data.root_path);
        setProjectPath(path);
        setFileTree(data.file_tree);
        setMainFile(data.main_file);
        setActiveFile(data.main_file);
        try {
          const p = await api.project.paragraphs(data.main_file, workspaceId);
          setParagraphs(p.paragraphs || []);
        } catch {
          setParagraphs([]);
        }
        toast.success(t("pages:workspace.projectOpened"), {
          description: data.root_path,
        });
      } catch (e) {
        const msg = translateError(e.message);
        setError(msg);
        toast.error(t("pages:workspace.openFailed"), { description: msg });
      } finally {
        setLoading(false);
      }
    },
    [
      t,
      workspaceId,
      setRootPath,
      setProjectPath,
      setFileTree,
      setMainFile,
      setActiveFile,
    ],
  );

  // 启动时：从 Zustand persist 恢复项目
  useEffect(() => {
    const restore = async () => {
      if (restoredRef.current) return;
      if (projectPath && fileTree.length === 0) {
        restoredRef.current = true;
        await doOpenProject(projectPath);
      }
    };
    restore();
  }, []);

  const handleOpenProject = useCallback(async () => {
    if (!openPath.trim()) return;
    await doOpenProject(openPath.trim());
  }, [openPath, doOpenProject]);

  const handleBrowseFolder = useCallback(async () => {
    setFolderPickerOpen(true);
  }, []);

  const handleFolderSelected = useCallback(
    async (path) => {
      setOpenPath(path);
      await doOpenProject(path);
    },
    [doOpenProject],
  );

  const handleMainFileChange = useCallback(
    async (path) => {
      setMainFile(path);
      try {
        await api.project.setMainFile(path, workspaceId);
      } catch (_) {
        // 忽略
      }
    },
    [setMainFile],
  );

  const handleCompile = useCallback(async () => {
    if (!mainFile) return;
    setCompileLoading(true);
    setCompileResult(null);
    setPdfUrl(null);
    try {
      const data = await api.compile.run(mainFile, workspaceId);
      setCompileResult(data);
      if (data.success) {
        setPdfUrl(
          `${api.compile.pdfUrl(mainFile, workspaceId)}&t=${Date.now()}`,
        );
        toast.success(t("pages:workspace.compileSuccessToast"));
      } else {
        toast.error(t("pages:workspace.compileFailedToast"), {
          description:
            (data.explanation && translateError(data.explanation)) ||
            t("pages:workspace.compileFailedToast"),
        });
      }
    } catch (e) {
      const msg = translateError(e.message);
      setCompileResult({
        success: false,
        explanation: msg,
        fixes: [],
        stdout: "",
        stderr: "",
      });
      toast.error(t("pages:workspace.compileFailedToast"), {
        description: msg,
      });
    } finally {
      setCompileLoading(false);
    }
  }, [
    mainFile,
    setCompileLoading,
    setCompileResult,
    setPdfUrl,
    t,
    workspaceId,
  ]);

  const handleSelectFile = useCallback(async (node) => {
    if (node.is_dir) return;
    setActiveFile(node.path);
    try {
      const data = await api.project.paragraphs(node.path, workspaceId);
      setParagraphs(data.paragraphs || []);
    } catch (e) {
      setParagraphs([]);
    }
  }, []);

  const reloadActiveFileParagraphs = useCallback(async () => {
    if (!activeFile) return;
    try {
      const data = await api.project.paragraphs(activeFile, workspaceId);
      setParagraphs(data.paragraphs || []);
    } catch (_) {
      setParagraphs([]);
    }
  }, [activeFile]);

  const {
    canUndo,
    canRedo,
    busy: undoBusy,
    handleUndo,
    handleRedo,
  } = useUndoRedo(activeFile, workspaceId, reloadActiveFileParagraphs);

  // 键盘快捷键：Ctrl+Z 撤销、Ctrl+Shift+Z / Ctrl+Y 重做
  // 输入框/文本域中不拦截，保留原生 undo 行为
  useEffect(() => {
    if (!projectPath) return;
    const onKey = (e) => {
      const target = e.target;
      const tag = target?.tagName;
      const isEditable =
        tag === "INPUT" || tag === "TEXTAREA" || target?.isContentEditable;
      if (isEditable) return;
      if (!(e.ctrlKey || e.metaKey)) return;
      const key = e.key.toLowerCase();
      if (key === "z" && !e.shiftKey) {
        e.preventDefault();
        handleUndo();
      } else if ((key === "z" && e.shiftKey) || key === "y") {
        e.preventDefault();
        handleRedo();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [projectPath, handleUndo, handleRedo]);

  return (
    <div className="min-h-screen p-8 animate-tab-enter">
      <div className="max-w-6xl mx-auto">
        <PageHeader title={t("pages:workspace.title")} />
      </div>

      <div className="max-w-6xl mx-auto">
        {!projectPath ? (
          <div className="flex justify-center mt-12">
            <Card className="max-w-lg w-full">
              <CardHeader>
                <CardTitle>{t("pages:workspace.openProject")}</CardTitle>
              </CardHeader>
              <CardContent className="space-y-4">
                <div className="space-y-2">
                  <Label htmlFor="project-path">
                    {t("pages:workspace.projectPath")}
                  </Label>
                  <div className="flex gap-2">
                    <Input
                      id="project-path"
                      className="flex-1"
                      value={openPath}
                      onChange={(e) => setOpenPath(e.target.value)}
                      placeholder={t("pages:workspace.pathPlaceholder")}
                      onKeyDown={(e) =>
                        e.key === "Enter" && handleOpenProject()
                      }
                    />
                    <Button
                      variant="outline"
                      onClick={handleBrowseFolder}
                      disabled={loading}
                      type="button"
                    >
                      {t("pages:workspace.browse")}
                    </Button>
                  </div>
                  <p className="text-xs text-muted-foreground">
                    {t("pages:workspace.pathHint")}
                  </p>
                </div>
                {error && <p className="text-destructive text-sm">{error}</p>}
                <Button
                  className="w-full"
                  onClick={handleOpenProject}
                  disabled={loading || !openPath.trim()}
                >
                  <FolderOpen />
                  {loading
                    ? t("pages:workspace.openBtnLoading")
                    : t("pages:workspace.openBtn")}
                </Button>
              </CardContent>
            </Card>
          </div>
        ) : (
          <>
            {/* 编译工具栏 */}
            <div className="flex items-center gap-3 mb-4">
              <Label className="text-sm whitespace-nowrap">
                {t("pages:workspace.mainFile")}
              </Label>
              <Select
                value={mainFile || ""}
                onValueChange={handleMainFileChange}
              >
                <SelectTrigger className="w-64">
                  <SelectValue
                    placeholder={t("pages:workspace.selectMainFile")}
                  />
                </SelectTrigger>
                <SelectContent>
                  {texFiles.map((f) => (
                    <SelectItem key={f.path} value={f.path}>
                      {f.path}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              {enabledFeatures?.compile && (
                <Button
                  onClick={handleCompile}
                  disabled={!mainFile || compileLoading}
                  size="sm"
                >
                  {compileLoading ? (
                    <>
                      <Loader2 className="animate-spin" />{" "}
                      {t("pages:workspace.compileBtnLoading")}
                    </>
                  ) : (
                    <>
                      <Play /> {t("pages:workspace.compileBtn")}
                    </>
                  )}
                </Button>
              )}
              {activeFile && (
                <div className="flex items-center gap-1 ml-auto">
                  <Tooltip>
                    <TooltipTrigger
                      render={
                        <Button
                          variant="outline"
                          size="icon"
                          onClick={() => setConfirmUndoOpen(true)}
                          disabled={!canUndo || undoBusy}
                        />
                      }
                    >
                      <Undo2 />
                    </TooltipTrigger>
                    <TooltipContent>{t("pages:workspace.undo")}</TooltipContent>
                  </Tooltip>
                  <Tooltip>
                    <TooltipTrigger
                      render={
                        <Button
                          variant="outline"
                          size="icon"
                          onClick={handleRedo}
                          disabled={!canRedo || undoBusy}
                        />
                      }
                    >
                      <Redo2 />
                    </TooltipTrigger>
                    <TooltipContent>{t("pages:workspace.redo")}</TooltipContent>
                  </Tooltip>
                </div>
              )}
              {enabledFeatures?.git_diff !== false && (
                <Button
                  variant="outline"
                  size="sm"
                  nativeButton={false}
                  render={<Link to="/git-diff" />}
                >
                  <FileDiff />
                  {t("pages:workspace.diffHighlight")}
                </Button>
              )}
            </div>

            <div className="grid grid-cols-4 gap-6">
              {/* file tree */}
              <div className="col-span-1">
                <Card>
                  <CardHeader className="pb-2">
                    <CardTitle className="text-sm">
                      {t("pages:workspace.files")}
                    </CardTitle>
                  </CardHeader>
                  <CardContent className="p-2">
                    <ProjectExplorer
                      tree={fileTree}
                      onSelect={handleSelectFile}
                    />
                  </CardContent>
                </Card>
                <div className="mt-2 space-y-1">
                  <Button
                    variant="ghost"
                    size="sm"
                    className="w-full text-xs"
                    onClick={() => setConfirmCloseOpen(true)}
                  >
                    {t("pages:workspace.closeProject")}
                  </Button>
                </div>
              </div>

              {/* paragraph viewer */}
              <div className="col-span-3">
                <Card>
                  <CardHeader className="pb-2">
                    <div className="flex items-center justify-between">
                      <div>
                        <CardTitle className="text-sm">
                          {activeFile || t("pages:workspace.selectFile")}
                        </CardTitle>
                        <CardDescription>
                          {rootPath && (
                            <span className="text-xs">{rootPath}</span>
                          )}
                        </CardDescription>
                      </div>
                      {activeFile && activeFile.endsWith(".tex") && (
                        <div className="flex items-center gap-1">
                          <Tooltip>
                            <TooltipTrigger
                              render={
                                <Button
                                  variant="outline"
                                  size="sm"
                                  nativeButton={false}
                                  render={<Link to="/typo-check" />}
                                />
                              }
                            >
                              <SpellCheck />
                            </TooltipTrigger>
                            <TooltipContent>
                              {t("pages:workspace.typoCheck")}
                            </TooltipContent>
                          </Tooltip>
                          <Tooltip>
                            <TooltipTrigger
                              render={
                                <Button
                                  variant="outline"
                                  size="sm"
                                  nativeButton={false}
                                  render={<Link to="/terminology-check" />}
                                />
                              }
                            >
                              <FileTypeCorner />
                            </TooltipTrigger>
                            <TooltipContent>
                              {t("pages:workspace.terminologyCheck")}
                            </TooltipContent>
                          </Tooltip>
                        </div>
                      )}
                    </div>
                  </CardHeader>
                  <CardContent>
                    {paragraphs.length === 0 ? (
                      <p className="text-muted-foreground text-sm">
                        {activeFile
                          ? t("pages:workspace.noParagraphs")
                          : t("pages:workspace.noParagraphsHint")}
                      </p>
                    ) : (
                      <div className="space-y-4">
                        {paragraphs.map((p) => (
                          <div
                            key={p.index}
                            className="rounded-lg border border-border p-3"
                          >
                            <div className="flex items-center gap-2 mb-2">
                              <span className="rounded bg-muted px-1.5 py-0.5 text-xs font-mono">
                                P{p.index}
                              </span>
                              <span className="text-xs text-muted-foreground">
                                {t("pages:workspace.lines", {
                                  start: p.line_start,
                                  end: p.line_end,
                                })}
                              </span>
                            </div>
                            <CodeBlock
                              code={p.text}
                              language="latex"
                              variant="bare"
                              className="text-sm text-foreground/80"
                            />
                          </div>
                        ))}
                      </div>
                    )}
                  </CardContent>
                </Card>
              </div>
            </div>

            {/* 编译结果 */}
            {compileResult && (
              <Card className="mt-4">
                <CardHeader>
                  <CardTitle className="text-sm">
                    {t("pages:workspace.compileResult")}
                  </CardTitle>
                  <CardAction>
                    <Badge
                      variant={
                        compileResult.success ? "secondary" : "destructive"
                      }
                    >
                      {compileResult.success ? (
                        <>
                          <Check /> {t("pages:workspace.compileSuccess")}
                        </>
                      ) : (
                        <>
                          <CircleX /> {t("pages:workspace.compileFailed")}
                        </>
                      )}
                    </Badge>
                  </CardAction>
                </CardHeader>
                <CardContent className="space-y-3">
                  {compileResult.explanation && (
                    <div>
                      <Label className="text-xs text-muted-foreground">
                        {t("pages:workspace.errorExplanation")}
                      </Label>
                      <p className="text-sm mt-1 whitespace-pre-wrap">
                        {translateError(compileResult.explanation)}
                      </p>
                    </div>
                  )}
                  {compileResult.fixes && compileResult.fixes.length > 0 && (
                    <div>
                      <Label className="text-xs text-muted-foreground">
                        {t("pages:workspace.fixSuggestion")}
                      </Label>
                      <ul className="text-sm mt-1 list-disc list-inside space-y-1">
                        {compileResult.fixes.map((fix, i) => (
                          <li key={i}>{fix}</li>
                        ))}
                      </ul>
                    </div>
                  )}
                  <div>
                    <Button
                      variant="ghost"
                      size="xs"
                      onClick={() => setShowRawOutput((v) => !v)}
                    >
                      {showRawOutput ? <ChevronDown /> : <ChevronRight />}
                      {t("pages:workspace.viewRawOutput")}
                    </Button>
                    {showRawOutput && (
                      <CodeBlock
                        code={`${compileResult.stdout || t("pages:workspace.noStdout")}\n${compileResult.stderr || t("pages:workspace.noStderr")}`}
                        language="plaintext"
                        className="mt-2 p-2 max-h-48"
                      />
                    )}
                  </div>
                </CardContent>
              </Card>
            )}

            {/* 编译成功后内联 PDF 预览 */}
            {compileResult?.success && pdfUrl && (
              <Card className="mt-4">
                <CardHeader className="pb-2">
                  <CardTitle className="text-sm">
                    {t("pages:workspace.pdfPreview")}
                  </CardTitle>
                </CardHeader>
                <CardContent>
                  <div className="h-[70vh]">
                    <PDFPreview url={pdfUrl} />
                  </div>
                </CardContent>
              </Card>
            )}
          </>
        )}
      </div>

      <ConfirmDialog
        open={confirmCloseOpen}
        onOpenChange={setConfirmCloseOpen}
        title={t("pages:workspace.closeConfirmTitle")}
        description={t("pages:workspace.closeConfirmDesc")}
        confirmText={t("pages:workspace.closeConfirmBtn")}
        variant="destructive"
        onConfirm={handleCloseProject}
      />
      <ConfirmDialog
        open={confirmUndoOpen}
        onOpenChange={setConfirmUndoOpen}
        title={t("pages:workspace.undoConfirmTitle")}
        description={t("pages:workspace.undoConfirmDesc")}
        confirmText={t("pages:workspace.undoConfirmBtn")}
        onConfirm={async () => {
          await handleUndo();
          setConfirmUndoOpen(false);
        }}
      />
      <FolderPickerDialog
        open={folderPickerOpen}
        onOpenChange={setFolderPickerOpen}
        onSelect={handleFolderSelected}
        initialPath={openPath || ""}
      />
    </div>
  );
}
