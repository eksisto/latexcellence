import { useState, useCallback, useEffect } from "react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Folder, ChevronUp, Loader2, FolderOpen } from "lucide-react";
import { api } from "@/api/index";

/**
 * 文件夹选择对话框。
 *
 * 通过后端 /api/project/browse 接口浏览本地目录，
 * 用户可逐级进入子目录，最终选中一个文件夹。
 *
 * @param {boolean}  open          受控开关
 * @param {(v:boolean)=>void} onOpenChange  开关回调
 * @param {(path:string)=>void} onSelect    选中文件夹回调（返回绝对路径）
 * @param {string}   initialPath   初始路径（可选）
 */
export default function FolderPickerDialog({
  open,
  onOpenChange,
  onSelect,
  initialPath = "",
}) {
  const [currentPath, setCurrentPath] = useState(initialPath);
  const [inputValue, setInputValue] = useState(initialPath);
  const [directories, setDirectories] = useState([]);
  const [parentPath, setParentPath] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  const fetchDirectories = useCallback(async (path) => {
    setLoading(true);
    setError(null);
    try {
      const data = await api.project.browse(path);
      setCurrentPath(data.current);
      setInputValue(data.current);
      setParentPath(data.parent);
      setDirectories(data.directories);
    } catch (e) {
      setError(e.message);
      setDirectories([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (open) {
      fetchDirectories(initialPath);
    }
  }, [open, initialPath, fetchDirectories]);

  const handleNavigate = useCallback((dirPath) => {
    fetchDirectories(dirPath);
  }, [fetchDirectories]);

  const handleGoUp = useCallback(() => {
    if (parentPath) {
      fetchDirectories(parentPath);
    }
  }, [parentPath, fetchDirectories]);

  const handleInputConfirm = useCallback(() => {
    const trimmed = inputValue.trim();
    if (trimmed) {
      fetchDirectories(trimmed);
    }
  }, [inputValue, fetchDirectories]);

  const handleSelect = useCallback(() => {
    if (currentPath && onSelect) {
      onSelect(currentPath);
      onOpenChange(false);
    }
  }, [currentPath, onSelect, onOpenChange]);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg" showCloseButton={false}>
        <DialogHeader>
          <DialogTitle>选择文件夹</DialogTitle>
          <DialogDescription>
            浏览并选择一个 LaTeX 项目目录
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-3">
          <div className="flex gap-2">
            <Input
              value={inputValue}
              onChange={(e) => setInputValue(e.target.value)}
              placeholder="输入路径后回车跳转"
              onKeyDown={(e) => e.key === "Enter" && handleInputConfirm()}
            />
            <Button
              variant="outline"
              onClick={handleInputConfirm}
              disabled={loading}
            >
              跳转
            </Button>
          </div>

          <div className="rounded-lg border border-border max-h-64 overflow-y-auto">
            {loading ? (
              <div className="flex items-center justify-center py-8 text-muted-foreground text-sm">
                <Loader2 className="animate-spin mr-2 size-4" />
                加载中...
              </div>
            ) : error ? (
              <div className="py-8 text-center text-destructive text-sm">
                {error}
              </div>
            ) : directories.length === 0 ? (
              <div className="py-8 text-center text-muted-foreground text-sm">
                此目录下没有子文件夹
              </div>
            ) : (
              <div className="divide-y divide-border">
                {parentPath && (
                  <button
                    className="flex items-center gap-2 w-full px-3 py-2 text-sm hover:bg-accent/50 transition-colors text-left"
                    onClick={handleGoUp}
                  >
                    <ChevronUp className="size-4 text-muted-foreground" />
                    <span className="text-muted-foreground">..</span>
                  </button>
                )}
                {directories.map((dir) => (
                  <button
                    key={dir.path}
                    className="flex items-center gap-2 w-full px-3 py-2 text-sm hover:bg-accent/50 transition-colors text-left"
                    onDoubleClick={() => handleNavigate(dir.path)}
                    onClick={() => handleNavigate(dir.path)}
                  >
                    <Folder className="size-4 text-primary/70 shrink-0" />
                    <span className="truncate">{dir.name}</span>
                  </button>
                ))}
              </div>
            )}
          </div>
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            取消
          </Button>
          <Button onClick={handleSelect} disabled={loading || !currentPath}>
            <FolderOpen />
            选择此文件夹
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
