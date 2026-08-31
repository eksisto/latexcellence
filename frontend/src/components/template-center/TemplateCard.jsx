import { useTranslation } from "react-i18next";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import Pencil from "lucide-react/dist/esm/icons/pencil.mjs";
import Trash2 from "lucide-react/dist/esm/icons/trash-2.mjs";
import { categoryLabelKey } from "@/utils/templateCenter";
import TemplateThumbnail from "@/components/template-center/TemplateThumbnail";

/**
 * 模板卡片：缩略图 + 名称 + 分类标签；内置模板带「内置」标识。
 * 「我的模板」中的卡片额外提供编辑（改名/分类）与删除操作。
 *
 * @param {object}   props.template     模板摘要（GET /api/templates 条目）
 * @param {() => void} props.onOpen     打开详情
 * @param {() => void} [props.onEdit]   编辑元数据（仅我的模板，未传则不显示）
 * @param {() => void} [props.onDelete] 删除（仅我的模板，未传则不显示）
 */
export default function TemplateCard({ template, onOpen, onEdit, onDelete }) {
  const { t } = useTranslation(["pages", "common"]);
  const isBuiltin = template.kind === "builtin";
  return (
    <Card className="group relative h-full cursor-pointer overflow-hidden transition-shadow hover:shadow-md">
      {/* 缩略图作为整卡点击区（避免按钮嵌套），操作按钮浮层单独阻断冒泡 */}
      <div
        role="button"
        tabIndex={0}
        onClick={onOpen}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            onOpen();
          }
        }}
        className="flex h-full flex-col focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/60"
      >
        <TemplateThumbnail
          template={template}
          className="aspect-[4/3] w-full"
        />
        <CardContent className="flex flex-1 flex-col gap-1.5 p-3">
          <div className="flex items-start justify-between gap-2">
            <p
              className="line-clamp-1 text-sm font-medium"
              title={template.name}
            >
              {template.name}
            </p>
            {isBuiltin && (
              <Badge variant="secondary" className="shrink-0">
                {t("pages:templateCenter.builtinBadge")}
              </Badge>
            )}
          </div>
          <div className="mt-auto flex items-center justify-between gap-2">
            <Badge variant="outline">
              {t(`pages:${categoryLabelKey(template.category)}`)}
            </Badge>
            <span className="text-xs text-muted-foreground">
              {t("pages:templateCenter.fileCount", {
                count: template.file_count ?? 0,
              })}
            </span>
          </div>
        </CardContent>
      </div>
      {!isBuiltin && (onEdit || onDelete) && (
        <div className="absolute right-2 top-2 flex gap-1 rounded-md bg-background/80 p-0.5 opacity-0 backdrop-blur transition-opacity group-hover:opacity-100 focus-within:opacity-100">
          {onEdit && (
            <Button
              variant="ghost"
              size="icon"
              className="size-7"
              onClick={(e) => {
                e.stopPropagation();
                onEdit();
              }}
            >
              <Pencil className="size-3.5" />
              <span className="sr-only">{t("pages:templateCenter.edit")}</span>
            </Button>
          )}
          {onDelete && (
            <Button
              variant="ghost"
              size="icon"
              className="size-7 text-destructive hover:text-destructive"
              onClick={(e) => {
                e.stopPropagation();
                onDelete();
              }}
            >
              <Trash2 className="size-3.5" />
              <span className="sr-only">{t("common:delete")}</span>
            </Button>
          )}
        </div>
      )}
    </Card>
  );
}
