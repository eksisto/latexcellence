import { Link } from "react-router-dom";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { ArrowLeft, Upload } from "lucide-react";

export default function FormulaOCRPage() {
  return (
    <div className="min-h-screen p-8">
      <div className="max-w-6xl mx-auto mb-8">
        <div className="flex items-center gap-4">
          <Button variant="ghost" size="icon" asChild>
            <Link to="/">
              <ArrowLeft />
            </Link>
          </Button>
          <h1 className="text-3xl font-bold tracking-tight">公式 OCR 识别</h1>
        </div>
      </div>

      <div className="max-w-2xl mx-auto">
        <div className="flex justify-center mt-12">
          <Card className="w-full">
            <CardHeader>
              <CardTitle>上传公式图像</CardTitle>
              <CardDescription>
                将手写或印刷的数学公式转换为 LaTeX 公式
              </CardDescription>
            </CardHeader>
            <CardContent>
              <div className="border-2 border-dashed border-border rounded-lg p-12 text-center cursor-pointer hover:border-ring/50 hover:bg-muted/50 transition-colors duration-300">
                <Upload className="size-10 mx-auto mb-3 text-muted-foreground" />
                <p className="text-muted-foreground">
                  点击或拖拽图片以上传
                </p>
                <p className="text-muted-foreground/60 text-sm mt-1">
                  支持 PNG、JPEG、WebP，最大 10MB
                </p>
              </div>
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
}
