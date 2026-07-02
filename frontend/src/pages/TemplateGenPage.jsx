import { Link } from "react-router-dom";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Textarea } from "@/components/ui/textarea";
import { ArrowLeft, Sparkles } from "lucide-react";

export default function TemplateGenPage() {
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
            模版生成
          </h1>
        </div>
      </div>

      <div className="max-w-2xl mx-auto">
        <div className="flex justify-center mt-12">
          <Card className="w-full">
            <CardHeader>
              <CardTitle>描述你的模版</CardTitle>
              <CardDescription>
                根据自然语言描述生成完整的 LaTeX 模板
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <Textarea
                className="min-h-32 resize-none"
                placeholder={
                  "描述你想要的模板风格，例如：\n" +
                  "IEEE 会议论文，两栏排版、摘要和参考文献……"
                }
              />
              <Button className="w-full">
                <Sparkles />
                生成模版
              </Button>
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
}
