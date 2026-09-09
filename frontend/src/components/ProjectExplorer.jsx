import { useState, useCallback, useEffect, useRef } from "react";
import { useTranslation } from "react-i18next";
import { cn } from "@/lib/utils";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  ContextMenu,
  ContextMenuContent,
  ContextMenuItem,
  ContextMenuSeparator,
  ContextMenuTrigger,
} from "@/components/ui/context-menu";
import {
  getFileTreeMenuItems,
  resolveNodeMenuTarget,
} from "@/lib/editor/contextMenuItems";
import Folder from "lucide-react/dist/esm/icons/folder.mjs";
import FolderOpen from "lucide-react/dist/esm/icons/folder-open.mjs";
import FolderPlus from "lucide-react/dist/esm/icons/folder-plus.mjs";
import File from "lucide-react/dist/esm/icons/file.mjs";
import FileText from "lucide-react/dist/esm/icons/file-text.mjs";
import Image from "lucide-react/dist/esm/icons/image.mjs";
import ChevronRight from "lucide-react/dist/esm/icons/chevron-right.mjs";
import Plus from "lucide-react/dist/esm/icons/plus.mjs";
import Star from "lucide-react/dist/esm/icons/star.mjs";
import Upload from "lucide-react/dist/esm/icons/upload.mjs";
import Loader2 from "lucide-react/dist/esm/icons/loader-2.mjs";

const IMAGE_FILE_RE = /\.(png|jpg|jpeg|gif|webp|bmp|pdf)$/i;
// 主文件仅限 .tex（与后端 set-main-file 的 allowed_suffixes 一致）
const isTexName = (name) => name.toLowerCase().endsWith(".tex");

/* ── 文件树拖拽移动（editor-file-move-design §5.1 / 决策 Q5、Q7） ──────
   原生 HTML5 Drag & Drop 零依赖：自定义 MIME 携带 {path, isDir}，
   落点为目录行（排除自身与子孙）与树背景空白区（= 项目根）。 */
const TREE_NODE_MIME = "application/x-latexcellence-tree-node";
/** 折叠目录上的悬停自动展开时延（决策 Q7） */
const AUTO_EXPAND_DELAY_MS = 500;
/** 根落点目录标记：树背景空白区 = 项目根（树内目录路径恒非空串） */
const ROOT_DROP_DIR = "";

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

/**
 * 菜单内容渲染（editor-file-context-menu-design §4.2）：按 Q13 纯函数
 * 产出的清单渲染菜单项，group 变化处插分隔线；纯文字不配图标（§3.1），
 * uploadingSensitive 项在上传进行中禁用（§3.2-5）。
 */
function FileTreeMenu({ target, uploading, onAction }) {
  const { t } = useTranslation("components");
  const nodes = [];
  let prevGroup = null;
  for (const item of getFileTreeMenuItems(target)) {
    if (prevGroup !== null && item.group !== prevGroup) {
      nodes.push(<ContextMenuSeparator key={`sep-${item.id}`} />);
    }
    prevGroup = item.group;
    nodes.push(
      <ContextMenuItem
        key={item.id}
        disabled={item.uploadingSensitive && uploading}
        onClick={() => onAction(item.id)}
      >
        {t(`projectExplorer.${item.i18nKey}`)}
      </ContextMenuItem>,
    );
  }
  return nodes;
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
  onCreateFolder,
  onDeleteFile,
  onSetMainFile,
  onRequestUpload,
  onRenameEntry,
  uploading,
  dnd,
}) {
  const { t } = useTranslation("components");
  const isDir = node.is_dir;
  const isExpanded = isDir && expandedDirs.has(node.path);
  const hasChildren = isDir && node.children && node.children.length > 0;
  // 每个节点各自比对：主文件可能在任意深度的子目录里，
  // 不能由父层算好下传（嵌套主文件会永远拿不到星标）
  const isMainFile = mainFile != null && node.path === mainFile;
  const isDragging = canWrite && dnd.isDragging(node.path);
  const isDropTarget = canWrite && dnd.isDropTarget(node.path);
  // 右键菜单开启态 = 行临时光（决策 Q8）：菜单关闭（执行项 / Esc / 点别处）
  // 即清除，树不引入持久选中概念
  const [menuOpen, setMenuOpen] = useState(false);

  const menuTarget = resolveNodeMenuTarget(node, mainFile);

  const handleMenuAction = useCallback(
    (actionId) => {
      switch (actionId) {
        case "newFile":
          onCreateFile?.(node.path);
          break;
        case "newFolder":
          onCreateFolder?.(node.path);
          break;
        case "uploadHere":
          onRequestUpload?.(node.path);
          break;
        case "setMainFile":
          onSetMainFile?.(node.path);
          break;
        case "rename":
          onRenameEntry?.(node.path, isDir);
          break;
        case "delete":
          onDeleteFile?.(node.path, isDir);
          break;
        default:
          break;
      }
    },
    [
      node.path,
      isDir,
      onCreateFile,
      onCreateFolder,
      onRequestUpload,
      onSetMainFile,
      onRenameEntry,
      onDeleteFile,
    ],
  );

  const row = (
    <div
      className={cn(
        "flex w-full items-center rounded-md",
        menuOpen && "bg-muted",
        isDragging && "opacity-50",
        isDropTarget && "ring-1 ring-primary/50 bg-primary/5",
      )}
      draggable={canWrite}
      onDragStart={(e) => dnd.onDragStart(e, node)}
      onDragEnd={dnd.onDragEnd}
      onDragOver={(e) => dnd.onDragOver(e, node)}
      onDragLeave={(e) => dnd.onDragLeave(e, node)}
      onDrop={(e) => dnd.onDrop(e, node)}
    >
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
      {isMainFile && (
        // 当前主文件：常驻实心星标（非按钮，决策 Q2），状态指示不是操作；
        // 单点操作入口收敛到右键菜单（决策 Q2）
        <span
          title={t("projectExplorer.currentMainFile")}
          className="mr-1 inline-flex size-5 shrink-0 items-center justify-center"
        >
          <Star className="size-3.5 fill-amber-400 text-amber-400" />
        </span>
      )}
    </div>
  );

  return (
    <li>
      {canWrite ? (
        // 右键即选中（决策 Q5a）+ 完全拦截原生菜单（Q5b，由 Trigger 的
        // stopEvent 天然完成）；目录只高亮不展开（目录展开仍在左键）
        <ContextMenu onOpenChange={setMenuOpen}>
          <ContextMenuTrigger render={row} />
          <ContextMenuContent>
            <FileTreeMenu
              target={menuTarget}
              uploading={uploading}
              onAction={handleMenuAction}
            />
          </ContextMenuContent>
        </ContextMenu>
      ) : (
        // viewer 不挂菜单（决策 Q6），原生菜单保留
        row
      )}
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
                onCreateFolder={onCreateFolder}
                onDeleteFile={onDeleteFile}
                onSetMainFile={onSetMainFile}
                onRequestUpload={onRequestUpload}
                onRenameEntry={onRenameEntry}
                uploading={uploading}
                dnd={dnd}
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
  onCreateFolder,
  onDeleteFile,
  onMoveEntry,
  onRenameEntry,
  onSetMainFile,
  onUploadFile,
  uploading = false,
}) {
  const { t } = useTranslation("components");
  const [expandedDirs, setExpandedDirs] = useState(() => new Set());
  // 上传选择框：内置隐藏 input 由按钮触发，uploadDirRef 记录触发入口
  // 对应的目标目录（null = 项目根，editor-file-upload-design §5.1 / D3）
  const uploadInputRef = useRef(null);
  const uploadDirRef = useRef(null);
  // 拖拽源（null = 无进行中的树内拖拽）：{ path, isDir }
  const [dragging, setDragging] = useState(null);
  // 高亮落点目录；ROOT_DROP_DIR（空串）= 树背景空白区，null = 无高亮
  const [dropTargetDir, setDropTargetDir] = useState(null);
  // 折叠目录悬停自动展开计时器（Q7）：{ path, timer } | null
  const hoverExpandRef = useRef(null);

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

  const openUploadPicker = useCallback((dirPath) => {
    uploadDirRef.current = dirPath;
    uploadInputRef.current?.click();
  }, []);

  const handleUploadInputChange = useCallback(
    (e) => {
      const { files } = e.target;
      if (files && files.length > 0)
        onUploadFile?.(uploadDirRef.current, files);
      // 复位以便再次选择同一文件仍触发 change
      e.target.value = "";
    },
    [onUploadFile],
  );

  /* ── 根级右键菜单（树背景空白区 / 头部标题栏共用，决策 Q9 / Q12） ──── */
  const handleRootMenuAction = useCallback(
    (actionId) => {
      switch (actionId) {
        case "newFile":
          onCreateFile?.(null);
          break;
        case "newFolder":
          onCreateFolder?.(null);
          break;
        case "upload":
          openUploadPicker(null);
          break;
        default:
          break;
      }
    },
    [onCreateFile, onCreateFolder, openUploadPicker],
  );

  /* ── 拖拽移动（editor-file-move-design §5.1） ────────────────────── */

  /** 落点合法性：目录行排除被拖目录自身与子孙（isDir 前缀判定）；
      文件落点与根目录恒合法（落回原位由 handleMoveEntry 静默忽略） */
  const canDropOn = useCallback((source, targetDir) => {
    if (!source) return false;
    if (targetDir == null) return true; // 项目根
    if (!source.isDir) return true;
    return (
      targetDir !== source.path && !targetDir.startsWith(`${source.path}/`)
    );
  }, []);

  const cancelHoverExpand = useCallback(() => {
    if (hoverExpandRef.current !== null) {
      window.clearTimeout(hoverExpandRef.current.timer);
      hoverExpandRef.current = null;
    }
  }, []);

  /** 折叠目录悬停约 500ms 自动展开（Q7）；已在同一节点计时则不重置 */
  const armHoverExpand = useCallback(
    (path) => {
      if (hoverExpandRef.current?.path === path) return;
      cancelHoverExpand();
      hoverExpandRef.current = {
        path,
        timer: window.setTimeout(() => {
          hoverExpandRef.current = null;
          setExpandedDirs((prev) => {
            // 展开即成，不再收起（与 toggleDir 的语义分离）
            if (prev.has(path)) return prev;
            const next = new Set(prev);
            next.add(path);
            return next;
          });
        }, AUTO_EXPAND_DELAY_MS),
      };
    },
    [cancelHoverExpand],
  );

  /** dragend / drop / 清理的统一复位：取消计时器并复位全部拖拽状态 */
  const clearDragState = useCallback(() => {
    cancelHoverExpand();
    setDragging(null);
    setDropTargetDir(null);
  }, [cancelHoverExpand]);

  // 卸载清理：拖拽进行中组件被卸载（左栏切大纲 / 路由离开）时取消悬停
  // 展开计时器并复位拖拽状态，防止卸载后计时器仍触发 setState
  useEffect(() => () => clearDragState(), [clearDragState]);

  const handleDragStart = useCallback(
    (e, node) => {
      if (!canWrite) {
        e.preventDefault();
        return;
      }
      e.dataTransfer.setData(
        TREE_NODE_MIME,
        JSON.stringify({ path: node.path, isDir: node.is_dir }),
      );
      e.dataTransfer.effectAllowed = "move";
      setDragging({ path: node.path, isDir: node.is_dir });
    },
    [canWrite],
  );

  const handleDragOver = useCallback(
    (e, node) => {
      // 目录行自管落点判定，阻断向树背景（根落点）冒泡
      e.stopPropagation();
      if (!dragging) return; // 外部拖入不做（§8 非目标）
      if (!node.is_dir) return; // 文件行不是落点：不 preventDefault → 禁止光标
      if (!canDropOn(dragging, node.path)) return; // 自身 / 子孙：非法落点
      e.preventDefault();
      e.dataTransfer.dropEffect = "move";
      setDropTargetDir((prev) => (prev === node.path ? prev : node.path));
      if (!expandedDirs.has(node.path)) armHoverExpand(node.path);
      else cancelHoverExpand();
    },
    [dragging, canDropOn, expandedDirs, armHoverExpand, cancelHoverExpand],
  );

  const handleDragLeave = useCallback(
    (e, node) => {
      e.stopPropagation();
      // 相关目标仍在行内（移动到子元素）时忽略，防止 dragleave 闪烁误清
      if (e.currentTarget.contains?.(e.relatedTarget)) return;
      cancelHoverExpand();
      setDropTargetDir((prev) => (prev === node.path ? null : prev));
    },
    [cancelHoverExpand],
  );

  const handleDrop = useCallback(
    (e, node) => {
      e.stopPropagation();
      e.preventDefault();
      if (dragging && node.is_dir && canDropOn(dragging, node.path)) {
        onMoveEntry?.(dragging.path, node.path, dragging.isDir);
      }
      clearDragState();
    },
    [dragging, canDropOn, onMoveEntry, clearDragState],
  );

  /** 树背景空白区 = 项目根落点（editor-file-move-design §5.1） */
  const handleRootDragOver = useCallback(
    (e) => {
      if (!dragging) return;
      e.preventDefault();
      e.dataTransfer.dropEffect = "move";
      setDropTargetDir((prev) =>
        prev === ROOT_DROP_DIR ? prev : ROOT_DROP_DIR,
      );
    },
    [dragging],
  );

  const handleRootDragLeave = useCallback((e) => {
    // 仍在树区内（移动到行等子元素）时忽略
    if (e.currentTarget.contains?.(e.relatedTarget)) return;
    setDropTargetDir((prev) => (prev === ROOT_DROP_DIR ? null : prev));
  }, []);

  const handleRootDrop = useCallback(
    (e) => {
      e.preventDefault();
      if (dragging) onMoveEntry?.(dragging.path, null, dragging.isDir);
      clearDragState();
    },
    [dragging, onMoveEntry, clearDragState],
  );

  // dnd 面向下传：TreeNode 递归子树共用的拖拽判定与视觉态
  const dnd = {
    isDragging: (path) => dragging?.path === path,
    isDropTarget: (path) => dropTargetDir === path,
    onDragStart: handleDragStart,
    onDragEnd: clearDragState,
    onDragOver: handleDragOver,
    onDragLeave: handleDragLeave,
    onDrop: handleDrop,
  };

  // 头部标题栏行（右键弹根级菜单，与树背景空白区一致，决策 Q9）
  const headerRow = (
    <div className="flex items-center justify-between mb-3 px-1">
      <h3 className="font-semibold text-sm text-muted-foreground">
        {t("projectExplorer.title")}
      </h3>
      {canWrite && tree.length > 0 && (
        // 头部根级三键保留（决策 Q2）：右键菜单成为单点操作主入口后的
        // 可发现性锚点
        <div className="flex shrink-0 items-center gap-1">
          <button
            type="button"
            title={t("projectExplorer.upload")}
            aria-label={t("projectExplorer.upload")}
            disabled={uploading}
            className={cn(
              "inline-flex size-5 items-center justify-center rounded",
              "text-muted-foreground hover:bg-muted hover:text-foreground",
              uploading && "pointer-events-none opacity-30",
            )}
            onClick={() => openUploadPicker(null)}
          >
            {uploading ? (
              <Loader2 className="size-4 animate-spin" />
            ) : (
              <Upload className="size-4" />
            )}
          </button>
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
          <button
            type="button"
            title={t("projectExplorer.createFolder")}
            aria-label={t("projectExplorer.createFolder")}
            className={cn(
              "inline-flex size-5 items-center justify-center rounded",
              "text-muted-foreground hover:bg-muted hover:text-foreground",
            )}
            onClick={() => onCreateFolder?.(null)}
          >
            <FolderPlus className="size-4" />
          </button>
        </div>
      )}
    </div>
  );

  // 树体（背景空白区 = 项目根落点；canWrite 时整体兼作根级右键 Trigger）
  const treeBody = (
    <div
      className={cn(
        "flex-1 min-h-0 rounded-md",
        dropTargetDir === ROOT_DROP_DIR &&
          "ring-1 ring-primary/50 bg-primary/5",
      )}
      onDragOver={handleRootDragOver}
      onDragLeave={handleRootDragLeave}
      onDrop={handleRootDrop}
    >
      <ScrollArea className="h-full">
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
              onCreateFolder={onCreateFolder}
              onDeleteFile={onDeleteFile}
              onSetMainFile={onSetMainFile}
              onRequestUpload={openUploadPicker}
              onRenameEntry={onRenameEntry}
              uploading={uploading}
              dnd={dnd}
            />
          ))}
        </ul>
      </ScrollArea>
    </div>
  );

  // 根级菜单（决策 Q12）：canWrite 且已开项目才挂载；空树 / viewer 不挂
  const rootMenuEnabled = canWrite && tree.length > 0;

  return (
    <div className="flex flex-col h-full">
      {/* 隐藏的多选文件输入：头部按钮与目录右键菜单共用同一入口 */}
      <input
        ref={uploadInputRef}
        type="file"
        multiple
        className="hidden"
        aria-hidden="true"
        tabIndex={-1}
        onChange={handleUploadInputChange}
      />
      {rootMenuEnabled ? (
        <ContextMenu>
          <ContextMenuTrigger render={headerRow} />
          <ContextMenuContent>
            <FileTreeMenu
              target={{ targetType: "root" }}
              uploading={uploading}
              onAction={handleRootMenuAction}
            />
          </ContextMenuContent>
        </ContextMenu>
      ) : (
        headerRow
      )}
      {tree.length === 0 ? (
        <p className="text-muted-foreground/60 text-sm px-1">
          {t("projectExplorer.noProject")}
        </p>
      ) : rootMenuEnabled ? (
        <ContextMenu>
          <ContextMenuTrigger render={treeBody} />
          <ContextMenuContent>
            <FileTreeMenu
              target={{ targetType: "root" }}
              uploading={uploading}
              onAction={handleRootMenuAction}
            />
          </ContextMenuContent>
        </ContextMenu>
      ) : (
        treeBody
      )}
    </div>
  );
}
