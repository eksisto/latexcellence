import { useTranslation } from "react-i18next";
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
  const { t } = useTranslation("components");
  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="text-sm font-medium">{t("promptEditor.title")}</CardTitle>
        <CardDescription className="text-xs">
          {t("promptEditor.description")}
        </CardDescription>
      </CardHeader>
      <CardContent>
        <Label className="sr-only" htmlFor="custom-prompt">
          {t("promptEditor.contentLabel")}
        </Label>
        <Textarea
          id="custom-prompt"
          className="min-h-28 resize-none text-sm"
          value={prompt || ""}
          onChange={(e) => onSave?.(e.target.value)}
          placeholder={t("promptEditor.placeholder")}
        />
      </CardContent>
    </Card>
  );
}
