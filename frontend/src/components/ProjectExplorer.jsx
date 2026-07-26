import { useState, useCallback } from "react";
import { useTranslation } from "react-i18next";
import { cn } from "@/lib/utils";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  Folder,
  FolderOpen,
  File,
  FileText,
  Image,
  ChevronRight,
} from "lucide-react";

function getFileIcon(name, isDir, isExpanded) {
  if (isDir) {
    const Icon = isExpanded ? FolderOpen : Folder;
    return <Icon className="size-4 text-muted-foreground" />;
  }
  if (name.endsWith(".tex"))
    return <FileText className="size-4 text-muted-foreground" />;
  if (/\.(png|jpg|jpeg|webp|pdf)$/i.test(name))
    return <Image className="size-4 text-muted-foreground" />;
  return <File className="size-4 text-muted-foreground" />;
}

function TreeNode({ node, depth = 0, expandedDirs, onToggleDir, onSelect }) {
  const isDir = node.is_dir;
  const isExpanded = isDir && expandedDirs.has(node.path);
  const hasChildren = isDir && node.children && node.children.length > 0;

  return (
    <li>
      <button
        className={cn(
          "flex items-center gap-1.5 w-full rounded-md px-2 py-1 text-sm text-left",
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
              />
            ))}
          </ul>
        </div>
      )}
    </li>
  );
}

export default function ProjectExplorer({ tree = [], onSelect }) {
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
      <h3 className="font-semibold text-sm text-muted-foreground mb-3 px-1">
        {t("projectExplorer.title")}
      </h3>
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
              />
            ))}
          </ul>
        </ScrollArea>
      )}
    </div>
  );
}
