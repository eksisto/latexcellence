import { useTranslation } from "react-i18next";
import Hourglass from "lucide-react/dist/esm/icons/hourglass.mjs";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import Wordmark from "@/components/Wordmark";
import AuthShell from "@/components/AuthShell";
import useStore from "@/store/useStore";
import { logoutUser } from "@/hooks/useAuth";

/**
 * 待管理员审批占位页（P5）。
 * 注册后账号为 pending，登录时后端拒绝并导向本页；也可直接访问。
 */
export default function PendingApprovalPage() {
  const { t } = useTranslation("pages");
  const authUser = useStore((s) => s.authUser);
  const username = authUser?.username || "";

  return (
    <AuthShell>
      <Card className="w-full max-w-sm">
        <CardHeader>
          <Wordmark className="text-xl" />
          <CardTitle className="mt-2 flex items-center gap-2">
            <Hourglass className="size-5" />
            {t("auth.pendingTitle")}
          </CardTitle>
          <CardDescription>
            {t("auth.pendingDesc", { username: username || "" })}
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <p className="text-sm text-muted-foreground">
            {t("auth.pendingHint")}
          </p>
          <Button
            variant="outline"
            className="w-full"
            onClick={async () => {
              await logoutUser();
            }}
          >
            {t("auth.pendingLogout")}
          </Button>
        </CardContent>
      </Card>
    </AuthShell>
  );
}
