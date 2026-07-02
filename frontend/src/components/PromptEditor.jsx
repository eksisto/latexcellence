import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

export default function PromptEditor({ prompt, onSave }) {
  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="text-sm font-medium">自定义提示词</CardTitle>
        <CardDescription className="text-xs">
          定义如何让 AI 优化你的文本
        </CardDescription>
      </CardHeader>
      <CardContent>
        <Label className="sr-only" htmlFor="custom-prompt">
          提示词内容
        </Label>
        <Textarea
          id="custom-prompt"
          className="min-h-28 resize-none text-sm"
          value={prompt || ""}
          onChange={(e) => onSave?.(e.target.value)}
          placeholder="输入你的自定义提示词..."
        />
      </CardContent>
    </Card>
  );
}
