import { useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import KeyRound from "lucide-react/dist/esm/icons/key-round.mjs";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import AuthShell from "@/components/AuthShell";
import { api } from "@/api/index";
import { translateError } from "@/utils/errorMessages";

/**
 * 自助找回密码——重置页（多用户 v2 P6）。
 * 从邮件链接跳转而来（/reset-password?token=...），设置新密码成功后
 * 服务端吊销该账号全部会话，导向登录页重新登录。
 */
export default function ResetPasswordPage() {
  const { t } = useTranslation("pages");
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const token = searchParams.get("token") || "";
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  const passwordMismatch = confirm && password !== confirm;

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!token || !password || !confirm) return;
    if (password !== confirm) {
      setError(t("auth.passwordMismatch"));
      return;
    }
    setLoading(true);
    setError(null);
    try {
      await api.auth.resetPassword(token, password);
      toast.success(t("auth.resetSuccess"));
      navigate("/login", { replace: true });
    } catch (err) {
      setError(translateError(err.message));
    } finally {
      setLoading(false);
    }
  };

  return (
    <AuthShell>
      <Card className="w-full max-w-sm gap-6">
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <KeyRound className="size-5 text-primary" />
            {t("auth.resetTitle")}
          </CardTitle>
          <CardDescription>{t("auth.resetDesc")}</CardDescription>
        </CardHeader>
        <CardContent>
          {!token ? (
            <div className="space-y-3">
              <p className="text-sm text-destructive" role="alert">
                {t("errors:resetTokenInvalid")}
              </p>
              <Button
                variant="outline"
                nativeButton={false}
                render={<Link to="/forgot-password" />}
              >
                {t("auth.backToForgot")}
              </Button>
            </div>
          ) : (
            <form className="space-y-4" onSubmit={handleSubmit}>
              <div className="space-y-1.5">
                <Label htmlFor="reset-password">{t("auth.newPassword")}</Label>
                <Input
                  id="reset-password"
                  type="password"
                  autoComplete="new-password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  placeholder={t("auth.passwordPlaceholder")}
                  disabled={loading}
                />
                <p className="text-xs text-muted-foreground">
                  {t("auth.passwordHint")}
                </p>
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="reset-confirm">
                  {t("auth.confirmPassword")}
                </Label>
                <Input
                  id="reset-confirm"
                  type="password"
                  autoComplete="new-password"
                  value={confirm}
                  onChange={(e) => setConfirm(e.target.value)}
                  placeholder={t("auth.confirmPasswordPlaceholder")}
                  disabled={loading}
                />
                {passwordMismatch ? (
                  <p className="text-xs text-destructive">
                    {t("auth.passwordMismatch")}
                  </p>
                ) : null}
              </div>
              {error ? (
                <p className="text-sm text-destructive" role="alert">
                  {error}
                </p>
              ) : null}
              <Button
                type="submit"
                className="w-full"
                disabled={
                  loading || !password || !confirm || Boolean(passwordMismatch)
                }
              >
                {loading ? (
                  <span className="inline-flex items-center gap-2">
                    <span className="size-4 animate-spin rounded-full border-2 border-current border-t-transparent" />
                    {t("auth.resetting")}
                  </span>
                ) : (
                  t("auth.resetSubmit")
                )}
              </Button>
            </form>
          )}
        </CardContent>
      </Card>
      <div className="mt-4 text-sm text-muted-foreground">
        {t("auth.haveAccount")}{" "}
        <Link
          to="/login"
          className="link-shrink inline-block hover:text-foreground"
        >
          {t("auth.backToLogin")}
        </Link>
      </div>
    </AuthShell>
  );
}
