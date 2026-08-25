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
import FolderOpen from "lucide-react/dist/esm/icons/folder-open.mjs";
import SpellCheck from "lucide-react/dist/esm/icons/spell-check.mjs";
import Play from "lucide-react/dist/esm/icons/play.mjs";
import Loader2 from "lucide-react/dist/esm/icons/loader-circle.mjs";
import Check from "lucide-react/dist/esm/icons/check.mjs";
import CircleX from "lucide-react/dist/esm/icons/circle-x.mjs";
import ChevronDown from "lucide-react/dist/esm/icons/chevron-down.mjs";
import ChevronRight from "lucide-react/dist/esm/icons/chevron-right.mjs";
import FileDiff from "lucide-react/dist/esm/icons/file-diff.mjs";
import FileTypeCorner from "lucide-react/dist/esm/icons/file-type-corner.mjs";
import { api } from "@/api/index";
import { isMultiUser } from "@/api/connection";
import { translateError } from "@/utils/errorMessages";
import { openMultiUserProject } from "@/utils/multiUserProject";
import useCanWrite from "@/hooks/useCanWrite";
import useStore from "@/store/useStore";
import useEnabledFeatures from "@/hooks/useEnabledFeatures";
import UndoRedoButtons from "@/components/UndoRedoButtons";
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
  const multiUser = isMultiUser();
  const projectPath = useStore((s) => s.projectPath);
  const projectId = useStore((s) => s.projectId);
  const canWrite = useCanWrite();
  const workspaceId = useStore((s) => s.workspaceId);
  const rootPath = useStore((s) => s.rootPath);
  const fileTree = useStore((s) => s.fileTree);
  const activeFile = useStore((s) => s.activeFile);
  const mainFile = useStore((s) => s.mainFile);
  const setProjectPath = useStore((s) => s.setProjectPath);
  const setRootPath = useStore((s) => s.setRootPath);
  const setProjectId = useStore((s) => s.setProjectId);
  const setProjectRole = useStore((s) => s.setProjectRole);
  const setFileTree = useStore((s) => s.setFileTree);
  const setActiveFile = useStore((s) => s.setActiveFile);
  const setMainFile = useStore((s) => s.setMainFile);
  const clearProject = useStore((s) => s.clearProject);
  const hydrated = useStore((s) => s.hydrated);
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
        setProjectId(null);
        setProjectRole(null);
        setFileTree(data.file_tree);
        setMainFile(data.main_file);
        setActiveFile(data.main_file);
        try {
          const p = await api.project.paragraphs(data.main_file, workspaceId);
          setParagraphs(p.paragraphs || []);
        } catch {
          setParagraphs([]);
        }
        // 单用户展示本地路径；多用户模式后端不返回 root_path（服务器路径不外露）
        toast.success(
          t("pages:workspace.projectOpened"),
          data.root_path ? { description: data.root_path } : undefined,
        );
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
      setProjectId,
      setProjectRole,
      setFileTree,
      setMainFile,
      setActiveFile,
    ],
  );

  /** 多用户模式：按 project_id 打开（拒绝任意路径），并同步最新角色 */
  const doOpenProjectById = useCallback(
    async (projectIdToOpen) => {
      setLoading(true);
      setError(null);
      try {
        // 统一打开入口：openById + 项目列表（同步角色）与主文档段落并行，
        // 消除"打开 → 列表 → 段落"的串行瀑布（原实现为 3 次串行往返）
        const { paragraphs } = await openMultiUserProject(
          projectIdToOpen,
          workspaceId,
          { withParagraphs: true },
        );
        setParagraphs(paragraphs);
        // 多用户模式：不展示服务器上的项目路径
        toast.success(t("pages:workspace.projectOpened"));
      } catch (e) {
        const msg = translateError(e.message);
        setError(msg);
        toast.error(t("pages:workspace.openFailed"), { description: msg });
      } finally {
        setLoading(false);
      }
    },
    [t, workspaceId],
  );

  const isOpened = multiUser ? !!projectId : !!projectPath;

  // 启动时：从 Zustand persist 恢复项目（等水合完成后再恢复）
  useEffect(() => {
    if (!hydrated || restoredRef.current) return;
    if (isOpened && fileTree.length === 0) {
      restoredRef.current = true;
      if (multiUser && projectId) {
        doOpenProjectById(projectId);
      } else if (projectPath) {
        doOpenProject(projectPath);
      }
    }
  }, [
    hydrated,
    isOpened,
    multiUser,
    projectId,
    projectPath,
    fileTree.length,
    doOpenProject,
    doOpenProjectById,
  ]);

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
    [setMainFile, workspaceId],
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
        const url = await api.compile.pdfBlobUrl(mainFile, workspaceId);
        setPdfUrl(url);
        toast.success(t("pages:workspace.compileSuccessToast"));
      } else {
        // 沙箱等新增错误码（如 sandbox_killed_oom）优先按码翻译；
        // translateError 的结构化入参在错误码未收录时自动回落 message 文本
        const reason =
          data.error_code || data.explanation
            ? translateError({
                error: data.error_code,
                message: data.explanation,
              })
            : "";
        toast.error(t("pages:workspace.compileFailedToast"), {
          description: reason || t("pages:workspace.compileFailedToast"),
        });
      }
    } catch (e) {
      // request() 会把结构化 detail.error 挂到 e.code（如 sandbox_unavailable），
      // 同样走结构化入参让错误码优先翻译，未收录时回落原消息文本
      const msg = translateError({ error: e.code, message: e.message });
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

  const handleSelectFile = useCallback(
    async (node) => {
      if (node.is_dir) return;
      setActiveFile(node.path);
      try {
        const data = await api.project.paragraphs(node.path, workspaceId);
        setParagraphs(data.paragraphs || []);
      } catch (e) {
        setParagraphs([]);
      }
    },
    [setActiveFile, workspaceId],
  );

  const reloadActiveFileParagraphs = useCallback(async () => {
    if (!activeFile) return;
    try {
      const data = await api.project.paragraphs(activeFile, workspaceId);
      setParagraphs(data.paragraphs || []);
    } catch (_) {
      setParagraphs([]);
    }
  }, [activeFile, workspaceId]);

  return (
    <div className="min-h-screen p-8 animate-tab-enter">
      <div className="max-w-6xl mx-auto">
        <PageHeader title={t("pages:workspace.title")} />
      </div>

      <div className="max-w-6xl mx-auto">
        {!isOpened ? (
          <div className="flex justify-center mt-12">
            <Card className="max-w-lg w-full">
              <CardHeader>
                <CardTitle>{t("pages:workspace.openProject")}</CardTitle>
              </CardHeader>
              {multiUser ? (
                <CardContent className="space-y-4">
                  <p className="text-sm text-muted-foreground">
                    {t("pages:workspace.multiUserOpenHint")}
                  </p>
                  <Button
                    className="w-full"
                    nativeButton={false}
                    render={<Link to="/projects" />}
                  >
                    <FolderOpen />
                    {t("pages:workspace.openFromProjects")}
                  </Button>
                </CardContent>
              ) : (
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
                  {error ? (
                    <p className="text-destructive text-sm">{error}</p>
                  ) : null}
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
              )}
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
                disabled={!canWrite}
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
              {activeFile && canWrite && (
                <UndoRedoButtons
                  activeFile={activeFile}
                  workspaceId={workspaceId}
                  onAfterUndoRedo={reloadActiveFileParagraphs}
                  className="ml-auto"
                />
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
                            className="rounded-lg border border-border bg-background p-3"
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
                        {/* 结构化错误码（如 sandbox_killed_oom）优先翻译，未收录回落说明文本 */}
                        {translateError({
                          error: compileResult.error_code,
                          message: compileResult.explanation,
                        })}
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
      <FolderPickerDialog
        open={folderPickerOpen}
        onOpenChange={setFolderPickerOpen}
        onSelect={handleFolderSelected}
        initialPath={openPath || ""}
      />
    </div>
  );
}
