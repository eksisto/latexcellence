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
        <CardTitle className="text-sm font-medium">Custom Prompt</CardTitle>
        <CardDescription className="text-xs">
          Define how the AI should optimize your text
        </CardDescription>
      </CardHeader>
      <CardContent>
        <Label className="sr-only" htmlFor="custom-prompt">
          Prompt content
        </Label>
        <Textarea
          id="custom-prompt"
          className="min-h-28 resize-none text-sm"
          value={prompt || ""}
          onChange={(e) => onSave?.(e.target.value)}
          placeholder="Enter your custom prompt..."
        />
      </CardContent>
    </Card>
  );
}
