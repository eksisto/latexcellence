import { useState, useCallback, useRef, useEffect } from "react";
import { Link } from "react-router-dom";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { ArrowLeft, Upload, Loader2, Copy, Table2 } from "lucide-react";
import { api } from "@/api/index";

const MAX_DISPLAY_SIZE = 400;

export default function TableOCRPage() {
  const [file, setFile] = useState(null);
  const [preview, setPreview] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [result, setResult] = useState(null);
  const [dragging, setDragging] = useState(false);
  const fileInputRef = useRef(null);

  const handleFile = useCallback((f) => {
    if (!f) return;
    if (!["image/png", "image/jpeg", "image/webp"].includes(f.type)) {
      setError("不支持的文件格式，请上传 PNG、JPEG 或 WebP");
      return;
    }
    setFile(f);
    setError(null);
    setResult(null);

    const reader = new FileReader();
    reader.onload = (e) => setPreview(e.target.result);
    reader.readAsDataURL(f);
  }, []);

  const handleInputChange = useCallback((e) => {
    handleFile(e.target.files?.[0]);
  }, [handleFile]);

  const handleDrop = useCallback((e) => {
    e.preventDefault();
    setDragging(false);
    handleFile(e.dataTransfer.files?.[0]);
  }, [handleFile]);

  const handleDragOver = useCallback((e) => {
    e.preventDefault();
    setDragging(true);
  }, []);

  const handleDragLeave = useCallback((e) => {
    e.preventDefault();
    setDragging(false);
  }, []);

  useEffect(() => {
    const onPaste = (e) => {
      const items = e.clipboardData?.items;
      if (!items) return;
      for (const item of items) {
        if (item.type.startsWith("image/")) {
          e.preventDefault();
          const blob = item.getAsFile();
          if (blob) {
            const f = new File([blob], "clipboard.png", { type: blob.type || "image/png" });
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
    setError(null);
    setResult(null);
    try {
      const formData = new FormData();
      formData.append("file", file);
      const data = await api.tableOCR.recognize(formData);
      setResult(data);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, [file]);

  const handleCopy = useCallback(() => {
    if (!result?.latex) return;
    navigator.clipboard.writeText(result.latex).catch(() => {});
  }, [result]);

  return (
    <div className="min-h-screen p-8">
      <div className="max-w-6xl mx-auto mb-8">
        <div className="flex items-center gap-4">
          <Button variant="ghost" size="icon" asChild>
            <Link to="/">
              <ArrowLeft />
            </Link>
          </Button>
          <h1 className="text-3xl font-bold tracking-tight">
            表格 OCR 识别
          </h1>
        </div>
      </div>

      <div className="max-w-2xl mx-auto space-y-4">
        <Card>
          <CardContent className="p-6">
            <input
              ref={fileInputRef}
              type="file"
              accept="image/png,image/jpeg,image/webp"
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
                    <Table2 />
                  )}
                  {loading ? "识别中..." : "识别表格"}
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => {
                    setFile(null);
                    setPreview(null);
                    setResult(null);
                    setError(null);
                  }}
                >
                  清除
                </Button>
              </div>
            )}

            {error && (
              <p className="text-sm text-destructive mt-2">{error}</p>
            )}
          </CardContent>
        </Card>

        {result && (
          <Card>
            <CardHeader className="pb-2">
              <div className="flex items-center justify-between">
                <CardTitle className="text-sm">
                  识别结果
                </CardTitle>
                <div className="flex items-center gap-2">
                  {result.low_confidence && (
                    <span className="rounded bg-yellow-500/10 text-yellow-600 dark:text-yellow-400 px-1.5 py-0.5 text-xs font-medium">
                      低置信度
                    </span>
                  )}
                  <span className="text-xs text-muted-foreground">
                    {(result.confidence * 100).toFixed(0)}% 置信度
                  </span>
                  <Button variant="outline" size="sm" onClick={handleCopy}>
                    <Copy />
                    Copy
                  </Button>
                </div>
              </div>
            </CardHeader>
            <CardContent>
              <pre className="whitespace-pre-wrap p-3 bg-muted rounded text-sm font-mono overflow-x-auto">
                {result.latex || "(empty)"}
              </pre>
            </CardContent>
          </Card>
        )}
      </div>
    </div>
  );
}
