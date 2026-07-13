import { useState, useCallback, useRef, useEffect } from "react";
import { Link } from "react-router-dom";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { ArrowLeft, Upload, Loader2, Copy } from "lucide-react";

const ACCEPTED_TYPES = ["image/png", "image/jpeg", "image/webp"];

/**
 * 通用图片 OCR 页面组件。
 *
 * 将 FormulaOCR 与 TableOCR 的共享逻辑（拖拽上传、粘贴、识别、复制）
 * 抽取为单一组件，通过 props 配置差异化的标题、图标、API 和结果展示。
 *
 * @param {object} props
 * @param {string} props.title              页面标题
 * @param {React.ComponentType} props.icon  识别按钮图标
 * @param {(formData: FormData) => Promise<object>} props.recognize  识别 API 调用
 * @param {string} props.recognizeButtonText  识别按钮文案
 * @param {(result: object) => React.ReactNode} [props.renderResultMeta]  结果头部额外信息
 * @param {string} [props.resultEmptyText="(empty)"]  结果为空时的占位文本
 */
export default function ImageOCRPage({
  title,
  icon: Icon,
  recognize,
  recognizeButtonText,
  renderResultMeta,
  resultEmptyText = "(empty)",
}) {
  const [file, setFile] = useState(null);
  const [preview, setPreview] = useState(null);
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState(null);
  const [dragging, setDragging] = useState(false);
  const fileInputRef = useRef(null);

  const handleFile = useCallback((f) => {
    if (!f) return;
    if (!ACCEPTED_TYPES.includes(f.type)) {
      toast.error("不支持的文件格式", {
        description: "请上传 PNG、JPEG 或 WebP",
      });
      return;
    }
    setFile(f);
    setResult(null);

    const reader = new FileReader();
    reader.onload = (e) => setPreview(e.target.result);
    reader.readAsDataURL(f);
  }, []);

  const handleInputChange = useCallback(
    (e) => {
      handleFile(e.target.files?.[0]);
    },
    [handleFile]
  );

  const handleDrop = useCallback(
    (e) => {
      e.preventDefault();
      setDragging(false);
      handleFile(e.dataTransfer.files?.[0]);
    },
    [handleFile]
  );

  const handleDragOver = useCallback((e) => {
    e.preventDefault();
    setDragging(true);
  }, []);

  const handleDragLeave = useCallback((e) => {
    e.preventDefault();
    setDragging(false);
  }, []);

  // Ctrl+V 粘贴剪切板图片
  useEffect(() => {
    const onPaste = (e) => {
      const items = e.clipboardData?.items;
      if (!items) return;
      for (const item of items) {
        if (item.type.startsWith("image/")) {
          e.preventDefault();
          const blob = item.getAsFile();
          if (blob) {
            const f = new File([blob], "clipboard.png", {
              type: blob.type || "image/png",
            });
            handleFile(f);
          }
          break;
        }
      }
    };
    document.addEventListener("paste", onPaste);
    return () => document.removeEventListener("paste", onPaste);
  }, [handleFile]);

  const handleRecognize = useCallback(async () => {
    if (!file) return;
    setLoading(true);
    setResult(null);
    try {
      const formData = new FormData();
      formData.append("file", file);
      const data = await recognize(formData);
      setResult(data);
      toast.success("识别完成");
    } catch (e) {
      toast.error("识别失败", { description: e.message });
    } finally {
      setLoading(false);
    }
  }, [file, recognize]);

  const handleCopy = useCallback(() => {
    if (!result?.latex) return;
    navigator.clipboard
      .writeText(result.latex)
      .then(() => toast.success("已复制到剪贴板"))
      .catch(() => toast.error("复制失败"));
  }, [result]);

  const handleClear = useCallback(() => {
    setFile(null);
    setPreview(null);
    setResult(null);
  }, []);

  return (
    <div className="min-h-screen p-8">
      <div className="max-w-6xl mx-auto mb-8">
        <div className="flex items-center gap-4">
          <Button variant="ghost" size="icon" nativeButton={false} render={<Link to="/" />}>
            <ArrowLeft />
          </Button>
          <h1 className="text-3xl font-bold tracking-tight">{title}</h1>
        </div>
      </div>

      <div className="max-w-2xl mx-auto space-y-4">
        {/* upload zone */}
        <Card>
          <CardContent className="p-6">
            <input
              ref={fileInputRef}
              type="file"
              accept={ACCEPTED_TYPES.join(",")}
              className="hidden"
              onChange={handleInputChange}
            />
            <div
              className={`border-2 border-dashed rounded-lg p-8 text-center cursor-pointer transition-colors
                ${dragging ? "border-primary bg-primary/5" : "border-border hover:border-muted-foreground/50"}`}
              onClick={() => fileInputRef.current?.click()}
              onDrop={handleDrop}
              onDragOver={handleDragOver}
              onDragLeave={handleDragLeave}
            >
              {preview ? (
                <div className="flex flex-col items-center gap-2">
                  <img
                    src={preview}
                    alt="Preview"
                    className="max-h-60 object-contain rounded"
                  />
                  <span className="text-xs text-muted-foreground">
                    {file?.name} ({(file?.size / 1024).toFixed(1)} KB)
                  </span>
                </div>
              ) : (
                <div className="flex flex-col items-center gap-2">
                  <Upload className="size-8 text-muted-foreground" />
                  <span className="text-sm">点击、拖拽 或 Ctrl+V 粘贴图片</span>
                  <span className="text-xs text-muted-foreground">
                    支持 PNG、JPEG、WebP，最大 10MB
                  </span>
                </div>
              )}
            </div>

            {file && (
              <div className="flex items-center gap-2 mt-3">
                <Button
                  onClick={handleRecognize}
                  disabled={loading}
                  className="flex-1"
                >
                  {loading ? (
                    <Loader2 className="animate-spin" />
                  ) : (
                    <Icon />
                  )}
                  {loading ? "识别中..." : recognizeButtonText}
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={handleClear}
                >
                  清除
                </Button>
              </div>
            )}
          </CardContent>
        </Card>

        {/* result */}
        {result && (
          <Card>
            <CardHeader className="pb-2">
              <div className="flex items-center justify-between">
                <CardTitle className="text-sm">识别结果</CardTitle>
                <div className="flex items-center gap-2">
                  {result.low_confidence && (
                    <span className="rounded bg-yellow-500/10 text-yellow-600 dark:text-yellow-400 px-1.5 py-0.5 text-xs font-medium">
                      低置信度
                    </span>
                  )}
                  {renderResultMeta && renderResultMeta(result)}
                  <Button variant="outline" size="sm" onClick={handleCopy}>
                    <Copy />
                    Copy
                  </Button>
                </div>
              </div>
            </CardHeader>
            <CardContent>
              <pre className="whitespace-pre-wrap p-3 bg-muted rounded text-sm font-mono overflow-x-auto">
                {result.latex || resultEmptyText}
              </pre>
            </CardContent>
          </Card>
        )}
      </div>
    </div>
  );
}
