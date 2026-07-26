import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Search, Filter, CornerDownRight, X } from "lucide-react";

/**
 * 段落检查结果过滤工具栏。
 *
 * 包含：文本搜索框、修改类型过滤器、段落编号跳转、重置按钮。
 * 与 useResultFilter hook 配套使用。
 *
 * @param {object} props
 * @param {string} props.searchQuery
 * @param {(v:string)=>void} props.setSearchQuery
 * @param {string} props.typeFilter
 * @param {(v:string)=>void} props.setTypeFilter
 * @param {string[]} props.availableTypes
 * @param {(idx:number)=>boolean} props.onJump  跳转回调，返回是否成功
 * @param {() => void} props.onReset
 * @param {boolean} props.hasFilters
 * @param {object} props.typeLabels  type → 中文标签映射（可选）
 */
export default function ResultFilterBar({
  searchQuery,
  setSearchQuery,
  typeFilter,
  setTypeFilter,
  availableTypes,
  onJump,
  onReset,
  hasFilters,
  typeLabels = {},
}) {
  const { t } = useTranslation("components");
  const [jumpInput, setJumpInput] = useState("");

  const handleJump = (e) => {
    e.preventDefault();
    const idx = parseInt(jumpInput, 10);
    if (Number.isNaN(idx)) return;
    onJump(idx);
  };

  return (
    <div className="flex flex-wrap items-center gap-2">
      <div className="relative flex-1 min-w-40">
        <Search className="absolute left-2 top-1/2 -translate-y-1/2 size-3.5 text-muted-foreground" />
        <Input
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          placeholder={t("resultFilterBar.searchPlaceholder")}
          className="pl-7 h-7 text-sm"
        />
      </div>
      {availableTypes.length > 0 && (
        <Select value={typeFilter} onValueChange={setTypeFilter}>
          <SelectTrigger className="h-7 w-36 text-sm">
            <Filter className="size-3.5" />
            <SelectValue>
              {typeFilter === "all"
                ? t("resultFilterBar.allTypes")
                : typeLabels[typeFilter] || typeFilter}
            </SelectValue>
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">{t("resultFilterBar.allTypes")}</SelectItem>
            {availableTypes.map((type) => (
              <SelectItem key={type} value={type}>
                {typeLabels[type] || type}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      )}
      <form onSubmit={handleJump} className="flex items-center gap-1">
        <Input
          value={jumpInput}
          onChange={(e) => setJumpInput(e.target.value)}
          placeholder={t("resultFilterBar.jumpTo")}
          className="h-7 w-24 text-sm"
          inputMode="numeric"
        />
        <Button
          type="submit"
          variant="ghost"
          size="icon-sm"
          title={t("resultFilterBar.jump")}
        >
          <CornerDownRight className="size-3.5" />
        </Button>
      </form>
      {hasFilters && (
        <Button
          variant="ghost"
          size="sm"
          onClick={onReset}
          title={t("resultFilterBar.clearFilter")}
        >
          <X className="size-3.5" />
          {t("resultFilterBar.clear")}
        </Button>
      )}
    </div>
  );
}
