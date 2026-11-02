import { useTranslation } from "react-i18next";
import FolderOpen from "lucide-react/dist/esm/icons/folder-open.mjs";
import { cn } from "@/lib/utils";
import { isMultiUser } from "@/api/connection";
import useIsOpened from "@/hooks/useIsOpened";
import useStore from "@/store/useStore";
import { resolveWorkspaceName } from "@/utils/projectName";

/**
 * 页头「当前工作区」小字：仅依赖项目已打开状态，未打开项目时不渲染。
 *
 * 名称来源：多用户取 `store.projectName`（项目列表/创建接口带回），
 * 单用户取项目路径最后一段；完整路径放在 title 提示中，避免长路径撑破页头。
 */
export default function WorkspaceBadge({ className }) {
  const { t } = useTranslation("common");
  const isOpened = useIsOpened();
  const projectName = useStore((s) => s.projectName);
  const projectPath = useStore((s) => s.projectPath);
  const rootPath = useStore((s) => s.rootPath);

  if (!isOpened) return null;

  const multiUser = isMultiUser();
  const name = resolveWorkspaceName({
    multiUser,
    projectName,
    projectPath,
    rootPath,
  });
  if (!name) return null;

  const tooltip = multiUser ? name : projectPath || rootPath || name;

  return (
    <span
      className={cn(
        "inline-flex max-w-64 min-w-0 shrink-0 items-center gap-1.5 text-xs font-normal tracking-normal text-muted-foreground",
        className,
      )}
      title={tooltip}
    >
      <FolderOpen className="size-3.5 shrink-0" aria-hidden="true" />
      <span className="truncate">{t("workspaceLabel", { name })}</span>
    </span>
  );
}
