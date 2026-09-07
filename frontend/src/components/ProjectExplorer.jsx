import { useState, useCallback } from "react";
import { useTranslation } from "react-i18next";
import { cn } from "@/lib/utils";
import { ScrollArea } from "@/components/ui/scroll-area";
import Folder from "lucide-react/dist/esm/icons/folder.mjs";
import FolderOpen from "lucide-react/dist/esm/icons/folder-open.mjs";
import File from "lucide-react/dist/esm/icons/file.mjs";
import FileText from "lucide-react/dist/esm/icons/file-text.mjs";
import Image from "lucide-react/dist/esm/icons/image.mjs";
import ChevronRight from "lucide-react/dist/esm/icons/chevron-right.mjs";
import Plus from "lucide-react/dist/esm/icons/plus.mjs";
import Trash2 from "lucide-react/dist/esm/icons/trash-2.mjs";
import Star from "lucide-react/dist/esm/icons/star.mjs";

const IMAGE_FILE_RE = /\.(png|jpg|jpeg|gif|webp|bmp|pdf)$/i;
// 主文件仅限 .tex（与后端 set-main-file 的 allowed_suffixes 一致）
const isTexName = (name) => name.toLowerCase().endsWith(".tex");

function getFileIcon(name, isDir, isExpanded) {
  if (isDir) {
    const Icon = isExpanded ? FolderOpen : Folder;
    return <Icon className="size-4 text-muted-foreground" />;
  }
  if (name.endsWith(".tex"))
    return <FileText className="size-4 text-muted-foreground" />;
  if (IMAGE_FILE_RE.test(name))
    return <Image className="size-4 text-muted-foreground" />;
  return <File className="size-4 text-muted-foreground" />;
}

/** 行内 hover 操作按钮（新建/删除）；stopPropagation 防触发行主点击 */
function RowActionButton({ title, onClick, children }) {
  return (
    <button
      type="button"
      title={title}
      aria-label={title}
      className={cn(
        "inline-flex size-5 shrink-0 items-center justify-center rounded",
        "text-muted-foreground hover:bg-muted hover:text-foreground",
        "opacity-0 transition-opacity group-hover:opacity-100 focus:opacity-100",
      )}
      onClick={(e) => {
        e.stopPropagation();
        onClick();
      }}
    >
      {children}
    </button>
  );
}

function TreeNode({
  node,
  depth = 0,
  expandedDirs,
  onToggleDir,
  onSelect,
  canWrite,
  mainFile,
  onCreateFile,
  onDeleteFile,
  onSetMainFile,
}) {
  const { t } = useTranslation("components");
  const isDir = node.is_dir;
  const isExpanded = isDir && expandedDirs.has(node.path);
  const hasChildren = isDir && node.children && node.children.length > 0;
  // 每个节点各自比对：主文件可能在任意深度的子目录里，
  // 不能由父层算好下传（嵌套主文件会永远拿不到星标）
  const isMainFile = mainFile != null && node.path === mainFile;

  return (
    <li>
      <div className="group flex w-full items-center">
        <button
          className={cn(
            "flex flex-1 items-center gap-1.5 min-w-0 cursor-pointer rounded-md px-2 py-1 text-sm text-left",
            "hover:bg-muted",
          )}
          style={{ paddingLeft: `${depth * 16 + 8}px` }}
          onClick={() => {
            if (isDir) {
              onToggleDir(node.path);
            } else {
              onSelect?.(node);
            }
          }}
        >
          <ChevronRight
            className={cn(
              "size-3 text-muted-foreground shrink-0 transition-transform duration-200 ease-out",
              isDir ? isExpanded && "rotate-90" : "invisible",
            )}
          />
          {getFileIcon(node.name, isDir, isExpanded)}
          <span className="truncate">{node.name}</span>
        </button>
        {canWrite && (
          <div className="flex shrink-0 items-center pr-1">
            {isDir ? (
              <RowActionButton
                title={t("projectExplorer.newFileHere")}
                onClick={() => onCreateFile?.(node.path)}
              >
                <Plus className="size-3.5" />
              </RowActionButton>
            ) : (
              <>
                {isTexName(node.name) &&
                  (isMainFile ? (
                    // 当前主文件：常驻实心星标（非按钮），提供状态可视性
                    <span
                      title={t("projectExplorer.currentMainFile")}
                      className="inline-flex size-5 shrink-0 items-center justify-center"
                    >
                      <Star className="size-3.5 fill-amber-400 text-amber-400" />
                    </span>
                  ) : (
                    <RowActionButton
                      title={t("projectExplorer.setMainFile")}
                      onClick={() => onSetMainFile?.(node.path)}
                    >
                      <Star className="size-3.5" />
                    </RowActionButton>
                  ))}
                <RowActionButton
                  title={t("projectExplorer.deleteFile")}
                  onClick={() => onDeleteFile?.(node.path)}
                >
                  <Trash2 className="size-3.5" />
                </RowActionButton>
              </>
            )}
          </div>
        )}
      </div>
      {hasChildren && (
        <div
          className="grid transition-[grid-template-rows] duration-200 ease-out"
          style={{ gridTemplateRows: isExpanded ? "1fr" : "0fr" }}
        >
          <ul className="overflow-hidden min-h-0">
            {node.children.map((child) => (
              <TreeNode
                key={child.path}
                node={child}
                depth={depth + 1}
                expandedDirs={expandedDirs}
                onToggleDir={onToggleDir}
                onSelect={onSelect}
                canWrite={canWrite}
                mainFile={mainFile}
                onCreateFile={onCreateFile}
                onDeleteFile={onDeleteFile}
                onSetMainFile={onSetMainFile}
              />
            ))}
          </ul>
        </div>
      )}
    </li>
  );
}

export default function ProjectExplorer({
  tree = [],
  onSelect,
  canWrite = false,
  mainFile = null,
  onCreateFile,
  onDeleteFile,
  onSetMainFile,
}) {
  const { t } = useTranslation("components");
  const [expandedDirs, setExpandedDirs] = useState(() => new Set());

  const toggleDir = useCallback((path) => {
    setExpandedDirs((prev) => {
      const next = new Set(prev);
      if (next.has(path)) {
        next.delete(path);
      } else {
        next.add(path);
      }
      return next;
    });
  }, []);

  return (
    <div className="flex flex-col h-full">
      <div className="flex items-center justify-between mb-3 px-1">
        <h3 className="font-semibold text-sm text-muted-foreground">
          {t("projectExplorer.title")}
        </h3>
        {canWrite && tree.length > 0 && (
          <button
            type="button"
            title={t("projectExplorer.newFile")}
            aria-label={t("projectExplorer.newFile")}
            className={cn(
              "inline-flex size-5 items-center justify-center rounded",
              "text-muted-foreground hover:bg-muted hover:text-foreground",
            )}
            onClick={() => onCreateFile?.(null)}
          >
            <Plus className="size-4" />
          </button>
        )}
      </div>
      {tree.length === 0 ? (
        <p className="text-muted-foreground/60 text-sm px-1">
          {t("projectExplorer.noProject")}
        </p>
      ) : (
        <ScrollArea className="flex-1">
          <ul className="space-y-0.5 pr-3">
            {tree.map((node) => (
              <TreeNode
                key={node.path}
                node={node}
                expandedDirs={expandedDirs}
                onToggleDir={toggleDir}
                onSelect={onSelect}
                canWrite={canWrite}
                mainFile={mainFile}
                onCreateFile={onCreateFile}
                onDeleteFile={onDeleteFile}
                onSetMainFile={onSetMainFile}
              />
            ))}
          </ul>
        </ScrollArea>
      )}
    </div>
  );
}
