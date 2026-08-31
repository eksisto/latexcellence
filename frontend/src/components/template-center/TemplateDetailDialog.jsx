import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import FolderTree from "lucide-react/dist/esm/icons/folder-tree.mjs";
import Folder from "lucide-react/dist/esm/icons/folder.mjs";
import FileText from "lucide-react/dist/esm/icons/file-text.mjs";
import ChevronLeft from "lucide-react/dist/esm/icons/chevron-left.mjs";
import Loader2 from "lucide-react/dist/esm/icons/loader-circle.mjs";
import FileWarning from "lucide-react/dist/esm/icons/file-warning.mjs";
import { api } from "@/api/index";
import { translateError } from "@/utils/errorMessages";
import { languageFromFilename } from "@/utils/highlight";
import {
  buildTemplateFileTree,
  categoryLabelKey,
  isViewableTemplateFile,
} from "@/utils/templateCenter";
import CodeBlock from "@/components/CodeBlock";
import PDFPreview from "@/components/PDFPreview";

/** 递归渲染文件树节点（目录在前；可查看源码的文件可点击） */
function FileTreeNodes({ nodes, activePath, onSelect }) {
  const { t } = useTranslation("pages");
  return (
    <ul className="space-y-0.5">
      {nodes.map((node) =>
        node.isDir ? (
          <li key={node.path}>
            <div className="flex items-center gap-1.5 px-1.5 py-1 text-sm text-muted-foreground">
              <Folder className="size-3.5 shrink-0" />
              <span className="truncate">{node.name}</span>
            </div>
            <div className="ml-3 border-l border-border pl-1.5">
              <FileTreeNodes
                nodes={node.children}
                activePath={activePath}
                onSelect={onSelect}
              />
            </div>
          </li>
        ) : (
          <li key={node.path}>
            <button
              type="button"
              onClick={() =>
                isViewableTemplateFile(node.path)
                  ? onSelect(node.path)
                  : undefined
              }
              disabled={!isViewableTemplateFile(node.path)}
              title={
                isViewableTemplateFile(node.path)
                  ? node.path
                  : t("templateCenter.notViewableFile")
              }
              className={`flex w-full items-center gap-1.5 rounded px-1.5 py-1 text-left text-sm font-mono transition-colors hover:bg-muted disabled:cursor-not-allowed disabled:opacity-50 ${
                activePath === node.path ? "bg-muted" : ""
              }`}
            >
              <FileText className="size-3.5 shrink-0 text-muted-foreground" />
              <span className="truncate">{node.name}</span>
            </button>
          </li>
        ),
      )}
    </ul>
  );
}

/** 信息行（标签 + 值） */
function InfoRow({ label, value }) {
  if (!value) return null;
  return (
    <div className="flex items-baseline gap-2 text-sm">
      <span className="shrink-0 text-xs text-muted-foreground">{label}</span>
      <span className="min-w-0 break-words">{value}</span>
    </div>
  );
}

/**
 * 模板详情对话框：内置模板左侧 PDF 预览 + 右侧元信息与文件树（点文件看源码）；
 * 用户模板无 PDF，仅文件树 + 源码查看。底部「使用此模板」触发创建流程。
 *
 * @param {object}  props.template 模板摘要
 * @param {boolean} props.open
 * @param {(v: boolean) => void} props.onOpenChange
 * @param {(template: object) => void} props.onUse 使用此模板
 */
export default function TemplateDetailDialog({
  template,
  open,
  onOpenChange,
  onUse,
}) {
  const { t } = useTranslation(["pages", "common"]);
  const [detail, setDetail] = useState(null);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState(null);
  const [previewUrl, setPreviewUrl] = useState(null);
  const [previewFailed, setPreviewFailed] = useState(false);
  const [selectedPath, setSelectedPath] = useState(null);
  const [fileContent, setFileContent] = useState("");
  const [fileLoading, setFileLoading] = useState(false);
  const [fileError, setFileError] = useState(null);
  // blob URL / 请求序号：对话框切换模板或快速点文件时丢弃过期响应
  const previewUrlRef = useRef(null);
  const fileSeqRef = useRef(0);

  const isBuiltin = template?.kind === "builtin";

  const releasePreview = useCallback(() => {
    if (previewUrlRef.current) {
      URL.revokeObjectURL(previewUrlRef.current);
      previewUrlRef.current = null;
    }
    setPreviewUrl(null);
  }, []);

  // 打开时拉取详情 + 内置模板的 PDF 预览 blob；关闭/切换模板时复位
  useEffect(() => {
    if (!open || !template) return;
    let cancelled = false;
    setLoading(true);
    setLoadError(null);
    setDetail(null);
    setSelectedPath(null);
    setFileContent("");
    setFileError(null);
    setPreviewFailed(false);
    releasePreview();
    (async () => {
      try {
        const data = await api.templateLibrary.detail(template.id);
        if (cancelled) return;
        setDetail(data);
      } catch (e) {
        if (cancelled) return;
        setLoadError(translateError({ error: e.code, message: e.message }));
      } finally {
        if (!cancelled) setLoading(false);
      }
      if (!cancelled && isBuiltin) {
        try {
          const url = await api.templateLibrary.previewBlobUrl(template.id);
          if (cancelled) {
            URL.revokeObjectURL(url);
            return;
          }
          previewUrlRef.current = url;
          setPreviewUrl(url);
        } catch {
          if (!cancelled) setPreviewFailed(true);
        }
      }
    })();
    return () => {
      cancelled = true;
      releasePreview();
    };
  }, [open, template, isBuiltin, releasePreview]);

  const handleSelectFile = useCallback(
    async (path) => {
      setSelectedPath(path);
      const seq = ++fileSeqRef.current;
      setFileLoading(true);
      setFileError(null);
      try {
        const data = await api.templateLibrary.fileContent(template.id, path);
        if (seq !== fileSeqRef.current) return;
        setFileContent(data.content ?? "");
      } catch (e) {
        if (seq !== fileSeqRef.current) return;
        setFileError(translateError({ error: e.code, message: e.message }));
        setFileContent("");
      } finally {
        if (seq === fileSeqRef.current) setFileLoading(false);
      }
    },
    [template],
  );

  const fileTree = detail ? buildTemplateFileTree(detail.files) : [];

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-4xl max-h-[85vh] flex flex-col">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <span className="truncate">{template?.name}</span>
            {isBuiltin && (
              <Badge variant="secondary" className="shrink-0">
                {t("pages:templateCenter.builtinBadge")}
              </Badge>
            )}
          </DialogTitle>
          {template?.name_en && template.name_en !== template.name ? (
            <DialogDescription>{template.name_en}</DialogDescription>
          ) : (
            <DialogDescription>
              {t("pages:templateCenter.detailTitle")}
            </DialogDescription>
          )}
        </DialogHeader>

        <div className="min-h-0 flex-1 overflow-y-auto pr-1 -mr-1">
          {loading ? (
            <div className="flex items-center justify-center gap-2 py-12 text-sm text-muted-foreground">
              <Loader2 className="size-4 animate-spin" />
              {t("common:loading")}
            </div>
          ) : loadError ? (
            <p className="py-12 text-center text-sm text-destructive">
              {loadError}
            </p>
          ) : (
            <div
              className={
                isBuiltin
                  ? "grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]"
                  : "space-y-4"
              }
            >
              {/* 左列：内置模板 PDF 预览（复用 PDFPreview，经认证 blob 加载） */}
              {isBuiltin && (
                <div className="h-[60vh]">
                  {previewUrl ? (
                    <PDFPreview url={previewUrl} />
                  ) : (
                    <Card className="h-full">
                      <CardContent className="flex h-full flex-col items-center justify-center gap-2">
                        <FileWarning className="size-8 text-muted-foreground/40" />
                        <p className="text-sm text-muted-foreground/60">
                          {previewFailed
                            ? t("pages:templateCenter.previewUnavailable")
                            : t("common:loading")}
                        </p>
                      </CardContent>
                    </Card>
                  )}
                </div>
              )}

              {/* 右列（或用户模板单列）：元信息 + 文件树 + 源码查看 */}
              <div className="flex min-h-0 flex-col gap-3">
                <div className="space-y-1.5 rounded-lg border border-border bg-muted/30 p-3">
                  <InfoRow
                    label={t("pages:templateCenter.descriptionLabel")}
                    value={detail?.description}
                  />
                  <InfoRow
                    label={t("pages:templateCenter.categoryLabel")}
                    value={
                      detail
                        ? t(`pages:${categoryLabelKey(detail.category)}`)
                        : null
                    }
                  />
                  <InfoRow
                    label={t("pages:templateCenter.authorLabel")}
                    value={detail?.author}
                  />
                  <InfoRow
                    label={t("pages:templateCenter.licenseLabel")}
                    value={detail?.license}
                  />
                  <InfoRow
                    label={t("pages:templateCenter.engineLabel")}
                    value={detail?.engine ? detail.engine.toUpperCase() : null}
                  />
                  <InfoRow
                    label={t("pages:templateCenter.mainFileLabel")}
                    value={detail?.main_file}
                  />
                  <InfoRow
                    label={t("pages:templateCenter.fileCountLabel")}
                    value={
                      detail
                        ? t("pages:templateCenter.fileCount", {
                            count:
                              detail.file_count ?? detail.files?.length ?? 0,
                          })
                        : null
                    }
                  />
                </div>

                <Card>
                  <CardHeader className="pb-1">
                    <CardTitle className="flex items-center gap-1.5 text-sm">
                      <FolderTree className="size-4" />
                      {t("pages:templateCenter.filesTitle")}
                    </CardTitle>
                  </CardHeader>
                  <CardContent className="p-2">
                    {selectedPath ? (
                      <div className="space-y-2">
                        <Button
                          variant="ghost"
                          size="sm"
                          className="h-7 px-2 text-xs"
                          onClick={() => setSelectedPath(null)}
                        >
                          <ChevronLeft className="size-3.5" />
                          {t("pages:templateCenter.backToFiles")}
                        </Button>
                        <p className="truncate font-mono text-xs text-muted-foreground">
                          {selectedPath}
                        </p>
                        {fileLoading ? (
                          <div className="flex items-center gap-2 py-6 text-sm text-muted-foreground">
                            <Loader2 className="size-4 animate-spin" />
                            {t("common:loading")}
                          </div>
                        ) : fileError ? (
                          <p className="py-6 text-sm text-destructive">
                            {fileError}
                          </p>
                        ) : (
                          <CodeBlock
                            code={fileContent}
                            language={languageFromFilename(selectedPath)}
                            className="p-3 max-h-[38vh] overflow-auto"
                          />
                        )}
                      </div>
                    ) : fileTree.length === 0 ? (
                      <p className="px-1.5 py-3 text-xs text-muted-foreground">
                        {t("pages:templateCenter.noFiles")}
                      </p>
                    ) : (
                      <div className="max-h-[38vh] overflow-y-auto">
                        <FileTreeNodes
                          nodes={fileTree}
                          activePath={selectedPath}
                          onSelect={handleSelectFile}
                        />
                      </div>
                    )}
                  </CardContent>
                </Card>
              </div>
            </div>
          )}
        </div>

        <DialogFooter className="mt-3">
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            {t("common:close")}
          </Button>
          <Button
            onClick={() => template && onUse(template)}
            disabled={loading || !!loadError}
          >
            {t("pages:templateCenter.useTemplate")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
