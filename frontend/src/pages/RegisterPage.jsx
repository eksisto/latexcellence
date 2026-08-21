import { useState } from "react";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import UserPlus from "lucide-react/dist/esm/icons/user-plus.mjs";
import CheckCircle from "lucide-react/dist/esm/icons/check-circle-2.mjs";
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
import useRegistrationEnabled from "@/hooks/useRegistrationEnabled";

/**
 * 多用户模式注册页（P5）。
 * 开放注册（registration_enabled）时可用；提交成功写入 pending，等待管理员审批。
 */
export default function RegisterPage() {
  const { t } = useTranslation("pages");
  // registration_enabled=false 时直接展示未开放提示，不再渲染注册表单
  const registrationEnabled = useRegistrationEnabled();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [email, setEmail] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [submitted, setSubmitted] = useState(false);

  const passwordMismatch = confirm && password !== confirm;

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!username.trim() || !password) return;
    if (password !== confirm) {
      setError(t("auth.passwordMismatch"));
      return;
    }
    setLoading(true);
    setError(null);
    try {
      await api.auth.register({
        username: username.trim(),
        password,
        email: email.trim(),
      });
      setSubmitted(true);
    } catch (err) {
      setError(translateError(err.message));
    } finally {
      setLoading(false);
    }
  };

  if (submitted) {
    return (
      <AuthShell>
        <Card className="w-full max-w-sm gap-6">
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <CheckCircle className="size-5 text-emerald-600" />
              {t("auth.registerSubmittedTitle")}
            </CardTitle>
            <CardDescription>
              {t("auth.registerSubmittedDesc", { username: username.trim() })}
            </CardDescription>
          </CardHeader>
          <CardContent>
            <p className="text-sm text-muted-foreground">
              {t("auth.registerSubmittedHint")}
            </p>
          </CardContent>
        </Card>
        <div className="mt-4">
          <Button
            variant="outline"
            nativeButton={false}
            render={<Link to="/login" />}
          >
            {t("auth.backToLogin")}
          </Button>
        </div>
      </AuthShell>
    );
  }

  if (registrationEnabled === false) {
    return (
      <AuthShell>
        <Card className="w-full max-w-sm gap-6">
          <CardHeader>
            <CardTitle>{t("auth.registerClosedTitle")}</CardTitle>
            <CardDescription>{t("auth.registerClosedDesc")}</CardDescription>
          </CardHeader>
        </Card>
        <div className="mt-4">
          <Button
            variant="outline"
            nativeButton={false}
            render={<Link to="/login" />}
          >
            {t("auth.backToLogin")}
          </Button>
        </div>
      </AuthShell>
    );
  }

  return (
    <AuthShell>
      <Card className="w-full max-w-sm gap-6">
        <CardHeader>
          <CardTitle>{t("auth.registerTitle")}</CardTitle>
          <CardDescription>{t("auth.registerDesc")}</CardDescription>
        </CardHeader>
        <CardContent>
          <form className="space-y-4" onSubmit={handleSubmit}>
            <div className="space-y-1.5">
              <Label htmlFor="reg-username">{t("auth.username")}</Label>
              <Input
                id="reg-username"
                autoComplete="username"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                placeholder={t("auth.usernamePlaceholder")}
                disabled={loading}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="reg-password">{t("auth.password")}</Label>
              <Input
                id="reg-password"
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
              <Label htmlFor="reg-confirm">{t("auth.confirmPassword")}</Label>
              <Input
                id="reg-confirm"
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
            <div className="space-y-1.5">
              <Label htmlFor="reg-email">
                {t("auth.email")}
                <span className="ml-1 text-muted-foreground/60">
                  {t("auth.optional")}
                </span>
              </Label>
              <Input
                id="reg-email"
                type="email"
                autoComplete="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder={t("auth.emailPlaceholder")}
                disabled={loading}
              />
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
                loading || !username.trim() || !password || passwordMismatch
              }
            >
              {loading ? (
                <span className="inline-flex items-center gap-2">
                  <span className="size-4 animate-spin rounded-full border-2 border-current border-t-transparent" />
                  {t("auth.registering")}
                </span>
              ) : (
                <>
                  <UserPlus /> {t("auth.registerBtn")}
                </>
              )}
            </Button>
          </form>
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
