import { useEffect, useRef, useState } from "react";
import { cn } from "@/lib/utils";
import FileText from "lucide-react/dist/esm/icons/file-text.mjs";
import { api } from "@/api/index";

/**
 * 模板卡片缩略图：内置模板经认证 fetch 拉取 /thumbnail 的临时 blob URL
 * （与 PDF 预览同一取数机制，多用户 Cookie 会话 / 单用户令牌头都由
 * fetchBlobUrl 统一处理）；用户模板与加载失败时回退文件图标占位。
 * 卸载 / 模板切换时释放 blob URL。
 */
export default function TemplateThumbnail({ template, className }) {
  const initialKind = template?.kind;
  const [blobUrl, setBlobUrl] = useState(null);
  // blob URL 归属标记：异步回包时模板可能已切换，仅接受当前模板的响应
  const urlRef = useRef(null);

  useEffect(() => {
    if (template?.kind !== "builtin") {
      // 切到用户模板：释放先前内置模板的 blob（与卸载释放同一兜底语义）
      if (urlRef.current) {
        URL.revokeObjectURL(urlRef.current);
        urlRef.current = null;
      }
      setBlobUrl(null);
      return;
    }
    let cancelled = false;
    (async () => {
      try {
        const url = await api.templateLibrary.thumbnailBlobUrl(template.id);
        if (cancelled) {
          URL.revokeObjectURL(url);
          return;
        }
        if (urlRef.current) URL.revokeObjectURL(urlRef.current);
        urlRef.current = url;
        setBlobUrl(url);
      } catch {
        // 缩略图缺失 / 资源 404：静默回退占位图标，不打断列表浏览
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [template?.id, template?.kind]);

  useEffect(
    () => () => {
      if (urlRef.current) {
        URL.revokeObjectURL(urlRef.current);
        urlRef.current = null;
      }
    },
    [],
  );

  if (initialKind !== "builtin" || !blobUrl) {
    return (
      <div
        className={cn(
          "flex items-center justify-center bg-muted/50 text-muted-foreground/50",
          className,
        )}
      >
        <FileText className="size-8" />
      </div>
    );
  }
  return (
    <img
      src={blobUrl}
      alt={template?.name || ""}
      className={cn("object-cover object-top", className)}
      loading="lazy"
    />
  );
}
