import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import Cpu from "lucide-react/dist/esm/icons/cpu.mjs";
import { api } from "@/api";
import useStore from "@/store/useStore";
import useUiStore, { SETTINGS_SECTION_MODELS } from "@/store/uiStore";
import { formatRouteDisplay, findRouteByRole } from "@/utils/modelProfiles";

/**
 * 功能入口处的当前模型徽标（multi-model-plan §7.2）。
 *
 * 展示该功能槽位两级解析后的「档案名 · 模型名」，数据源为
 * GET /api/llm/routes（多用户模式已登录即可读）；进入页面时拉取一次，
 * 不随每次请求刷新，拉取失败静默降级为不渲染（不弹错误）。
 *
 * 点击打开设置对话框并定位到模型管理分区；多用户普通用户对模型配置
 * 只读（设置接口仅管理员可见），徽标退化为纯展示（与 App.jsx 挂载
 * SettingsDialog 的条件同口径）。
 */
export default function ModelBadge({ role }) {
  const { t } = useTranslation("components");
  const [route, setRoute] = useState(null);
  const authStatus = useStore((s) => s.authStatus);
  const authUser = useStore((s) => s.authUser);
  const openSettingsDialog = useUiStore((s) => s.openSettingsDialog);
  // 设置保存成功后版本号自增，触发重新拉取（改派模型后徽标即时刷新）
  const routesVersion = useUiStore((s) => s.modelRoutesVersion);

  useEffect(() => {
    if (!role) return undefined;
    let cancelled = false;
    api.config
      .llmRoutes()
      .then((data) => {
        if (!cancelled) setRoute(findRouteByRole(data?.routes, role));
      })
      .catch(() => {
        /* 拉取失败静默降级：不显示徽标、不弹错误 */
      });
    return () => {
      cancelled = true;
    };
  }, [role, routesVersion]);

  const display = formatRouteDisplay(route);
  if (!display) return null;

  const canOpenSettings =
    authStatus === "single" ||
    (authStatus === "authenticated" && authUser?.role === "admin");

  if (!canOpenSettings) {
    return (
      <span
        className="inline-flex max-w-48 cursor-default items-center gap-1.5 rounded-full border border-border bg-muted/50 px-2.5 py-0.5 text-xs text-muted-foreground"
        title={t("modelBadge.readonlyHint")}
      >
        <Cpu className="size-3 shrink-0" />
        <span className="truncate">{display}</span>
      </span>
    );
  }

  return (
    <Tooltip>
      <TooltipTrigger
        render={
          <button
            type="button"
            onClick={() => openSettingsDialog(SETTINGS_SECTION_MODELS)}
            aria-label={`${t("modelBadge.currentModel")}: ${display}`}
            className="inline-flex max-w-48 cursor-pointer items-center gap-1.5 rounded-full border border-border bg-muted/50 px-2.5 py-0.5 text-xs text-muted-foreground outline-none transition-colors hover:border-muted-foreground/40 hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring/50"
          />
        }
      >
        <Cpu className="size-3 shrink-0" />
        <span className="truncate">{display}</span>
      </TooltipTrigger>
      <TooltipContent>{t("modelBadge.openSettings")}</TooltipContent>
    </Tooltip>
  );
}
