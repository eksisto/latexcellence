import { Link } from "react-router-dom";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { ArrowLeft } from "lucide-react";

export default function ExpressionOptPage() {
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
            表达优化
          </h1>
        </div>
      </div>

      <div className="max-w-4xl mx-auto">
        <div className="flex justify-center mt-12">
          <Card className="max-w-md w-full">
            <CardHeader>
              <CardTitle>优化表达</CardTitle>
              <CardDescription>
                AI 为你的学术写作风格提供优化建议
              </CardDescription>
            </CardHeader>
            <CardContent>
              <p className="text-muted-foreground text-sm">
                请先打开一个项目，才能进行表达优化。你也可以根据需要自定义优化提示词。
              </p>
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
}
