import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import LockKeyhole from "lucide-react/dist/esm/icons/lock-keyhole.mjs";
import LogIn from "lucide-react/dist/esm/icons/log-in.mjs";
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
import useStore from "@/store/useStore";
import { translateError } from "@/utils/errorMessages";

/**
 * 多用户模式登录页（P5）。
 * 登录成功后设置 Cookie 会话，恢复 /api/auth/me，跳转工作台。
 */
export default function LoginPage() {
  const { t } = useTranslation(["pages", "common"]);
  const navigate = useNavigate();
  const setAuthStatus = useStore((s) => s.setAuthStatus);
  const setAuthMe = useStore((s) => s.setAuthMe);

  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [remember, setRemember] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!username.trim() || !password) return;
    setLoading(true);
    setError(null);
    try {
      const data = await api.auth.login({
        username: username.trim(),
        password,
        remember,
      });
      const me = await api.auth.me();
      setAuthMe(me);
      const pending = me?.user?.status === "pending";
      setAuthStatus(pending ? "pending" : "authenticated");
      toast.success(
        t("pages:auth.loginSuccess", {
          username: data?.user?.username || username.trim(),
        }),
      );
      navigate(pending ? "/waiting" : "/projects", { replace: true });
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
          <CardTitle>{t("pages:auth.loginTitle")}</CardTitle>
          <CardDescription>{t("pages:auth.loginDesc")}</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <form className="space-y-4" onSubmit={handleSubmit}>
            <div className="space-y-1.5">
              <Label htmlFor="login-username">{t("pages:auth.username")}</Label>
              <Input
                id="login-username"
                autoComplete="username"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                placeholder={t("pages:auth.usernamePlaceholder")}
                disabled={loading}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="login-password">{t("pages:auth.password")}</Label>
              <Input
                id="login-password"
                type="password"
                autoComplete="current-password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder={t("pages:auth.passwordPlaceholder")}
                disabled={loading}
              />
            </div>
            <label className="flex items-center gap-2 text-sm text-muted-foreground">
              <input
                type="checkbox"
                checked={remember}
                onChange={(e) => setRemember(e.target.checked)}
                disabled={loading}
                className="size-4"
              />
              {t("pages:auth.rememberMe")}
            </label>
            {error ? (
              <p className="text-sm text-destructive" role="alert">
                {error}
              </p>
            ) : null}
            <Button
              type="submit"
              className="w-full"
              disabled={loading || !username.trim() || !password}
            >
              {loading ? (
                <span className="inline-flex items-center gap-2">
                  <span className="size-4 animate-spin rounded-full border-2 border-current border-t-transparent" />
                  {t("pages:auth.loggingIn")}
                </span>
              ) : (
                <>
                  <LogIn /> {t("pages:auth.loginBtn")}
                </>
              )}
            </Button>
          </form>
        </CardContent>
      </Card>
      <div className="mt-4 flex items-center justify-center gap-4 text-sm text-muted-foreground">
        <Link
          to="/register"
          className="link-shrink inline-flex items-center gap-1.5 hover:text-foreground"
        >
          <LockKeyhole className="size-4" />
          {t("pages:auth.needAccount")}
        </Link>
      </div>
    </AuthShell>
  );
}
