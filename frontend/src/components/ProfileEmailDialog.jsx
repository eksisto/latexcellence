import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { api } from "@/api/index";
import useStore from "@/store/useStore";
import { translateError } from "@/utils/errorMessages";

/**
 * 绑定/修改邮箱对话框（多用户 v2 P6）。
 * 需当前密码确认；email 传空字符串表示解绑。成功后刷新 /auth/me
 * 同步全局用户信息（authUser.email）。
 */
export default function ProfileEmailDialog({ open, onOpenChange }) {
  const { t } = useTranslation(["components", "pages"]);
  const authUser = useStore((s) => s.authUser);
  const setAuthMe = useStore((s) => s.setAuthMe);

  const [email, setEmail] = useState("");
  const [currentPassword, setCurrentPassword] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  // 打开时回填当前邮箱（掩码不涉及——邮箱本身对本人可见）
  useEffect(() => {
    if (open) {
      setEmail(authUser?.email || "");
      setCurrentPassword("");
      setError(null);
    }
  }, [open, authUser]);

  const handleSubmit = async () => {
    if (!currentPassword) return;
    setLoading(true);
    setError(null);
    try {
      await api.auth.updateProfile(currentPassword, email.trim());
      // 成功后拉取 /auth/me 同步用户与用量（保持全局单一事实源）
      const me = await api.auth.me().catch(() => null);
      if (me) setAuthMe(me);
      toast.success(t("pages:workbench.emailSaved"));
      onOpenChange(false);
    } catch (err) {
      setError(translateError(err.message));
    } finally {
      setLoading(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange} disablePointerDismissal>
      <DialogContent showCloseButton={false} className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{t("pages:workbench.emailDialogTitle")}</DialogTitle>
          <DialogDescription>
            {t("pages:workbench.emailDialogDesc")}
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-3 py-1">
          <div className="space-y-1.5">
            <Label htmlFor="profile-email">
              {t("pages:workbench.emailField")}
            </Label>
            <Input
              id="profile-email"
              type="email"
              autoComplete="email"
              value={email}
              placeholder={t("pages:workbench.emailPlaceholder")}
              onChange={(e) => setEmail(e.target.value)}
              disabled={loading}
            />
            <p className="text-xs text-muted-foreground">
              {t("pages:workbench.emailHint")}
            </p>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="profile-email-password">
              {t("pages:workbench.emailCurrentPassword")}
            </Label>
            <Input
              id="profile-email-password"
              type="password"
              autoComplete="current-password"
              value={currentPassword}
              onChange={(e) => setCurrentPassword(e.target.value)}
              disabled={loading}
            />
          </div>
          {error ? (
            <p className="text-sm text-destructive" role="alert">
              {error}
            </p>
          ) : null}
        </div>
        <DialogFooter>
          <Button
            variant="outline"
            onClick={() => onOpenChange(false)}
            disabled={loading}
          >
            {t("confirmDialog.cancel")}
          </Button>
          <Button onClick={handleSubmit} disabled={loading || !currentPassword}>
            {loading ? t("confirmDialog.processing") : t("common:save")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
