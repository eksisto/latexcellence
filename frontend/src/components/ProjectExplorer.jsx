import { cn } from "@/lib/utils";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  Folder,
  File,
  FileText,
  Image,
  ChevronRight,
} from "lucide-react";

function getFileIcon(name, isDir) {
  if (isDir) return <Folder className="size-4 text-muted-foreground" />;
  if (name.endsWith(".tex")) return <FileText className="size-4 text-muted-foreground" />;
  if (/\.(png|jpg|jpeg|webp|pdf)$/i.test(name))
    return <Image className="size-4 text-muted-foreground" />;
  return <File className="size-4 text-muted-foreground" />;
}

function TreeNode({ node, depth = 0, onSelect }) {
  return (
    <li>
      <button
        className={cn(
          "flex items-center gap-1.5 w-full rounded-md px-2 py-1 text-sm text-left",
          "hover:bg-muted"
        )}
        style={{ paddingLeft: `${depth * 16 + 8}px` }}
        onClick={() => onSelect?.(node)}
      >
        <ChevronRight
          className={cn(
            "size-3 text-muted-foreground shrink-0",
            !node.is_dir && "invisible"
          )}
        />
        {getFileIcon(node.name, node.is_dir)}
        <span className="truncate">{node.name}</span>
      </button>
      {node.children && node.children.length > 0 && (
        <ul>
          {node.children.map((child) => (
            <TreeNode
              key={child.path}
              node={child}
              depth={depth + 1}
              onSelect={onSelect}
            />
          ))}
        </ul>
      )}
    </li>
  );
}

export default function ProjectExplorer({ tree = [], onSelect }) {
  return (
    <div className="flex flex-col h-full">
      <h3 className="font-semibold text-sm text-muted-foreground mb-3 px-1">
        项目文件
      </h3>
      {tree.length === 0 ? (
        <p className="text-muted-foreground/60 text-sm px-1">
          No project opened
        </p>
      ) : (
        <ScrollArea className="flex-1">
          <ul className="space-y-0.5 pr-3">
            {tree.map((node) => (
              <TreeNode key={node.path} node={node} onSelect={onSelect} />
            ))}
          </ul>
        </ScrollArea>
      )}
    </div>
  );
}
