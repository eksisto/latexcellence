import { useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import Settings from "lucide-react/dist/esm/icons/settings.mjs";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import ConnectionPanel from "@/components/ConnectionPanel";
import { setApiConnection } from "@/api/connection";

/**
 * 认证页工具栏的「后端连接设置」入口（登录/注册/待审批页右上角）。
 *
 * 前端实例绑定的后端不可用或用户没有账号时，可在此改绑其他后端地址；
 * 样式与 AppToolbar 的主题切换等图标按钮一致。保存后刷新页面，
 * 重新走启动探测（显式绑定地址优先）。
 */
export default function AuthConnectionButton() {
  const { t } = useTranslation("components");
  const [open, setOpen] = useState(false);

  return (
    <>
      <Tooltip>
        <TooltipTrigger
          render={
            <Button variant="ghost" size="icon" onClick={() => setOpen(true)} />
          }
        >
          <Settings className="size-5" />
          <span className="sr-only">{t("settings.authConnectionButton")}</span>
        </TooltipTrigger>
        <TooltipContent>{t("settings.authConnectionButton")}</TooltipContent>
      </Tooltip>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>{t("settings.connection")}</DialogTitle>
            <DialogDescription>
              {t("settings.authConnectionDesc")}
            </DialogDescription>
          </DialogHeader>
          <ConnectionPanel
            open={open}
            onSaved={(apiBase, apiToken) => {
              setApiConnection(apiBase, apiToken);
              toast.success(t("settings.savedReloading"));
              setTimeout(() => window.location.reload(), 300);
            }}
          />
        </DialogContent>
      </Dialog>
    </>
  );
}
