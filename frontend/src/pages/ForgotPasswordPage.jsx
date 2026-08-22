import { useState } from "react";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import MailQuestion from "lucide-react/dist/esm/icons/mail-question.mjs";
import MailCheck from "lucide-react/dist/esm/icons/mail-check.mjs";
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
 * 自助找回密码——请求页（多用户 v2 P6）。
 * 输入用户名或邮箱提交；后端响应恒定语义防枚举：
 * - delivery=sent：邮件已投递（含令牌链接，1 小时内单次有效）
 * - delivery=unavailable：无法自助投递（账号未绑邮箱/邮箱重复/SMTP 未配置
 *   等统一归并），引导联系管理员，不泄露账号是否存在
 */
export default function ForgotPasswordPage() {
  const { t } = useTranslation("pages");
  const [identifier, setIdentifier] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  // null=未提交 | "sent" | "unavailable"
  const [result, setResult] = useState(null);

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!identifier.trim()) return;
    setLoading(true);
    setError(null);
    try {
      const data = await api.auth.forgotPassword(identifier.trim());
      setResult(data?.delivery === "sent" ? "sent" : "unavailable");
    } catch (err) {
      setError(translateError(err.message));
    } finally {
      setLoading(false);
    }
  };

  if (result === "sent") {
    return (
      <AuthShell>
        <Card className="w-full max-w-sm gap-6">
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <MailCheck className="size-5 text-emerald-600" />
              {t("auth.forgotSentTitle")}
            </CardTitle>
            <CardDescription>{t("auth.forgotSentDesc")}</CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            <p className="text-sm text-muted-foreground">
              {t("auth.forgotSentHint")}
            </p>
            <Button
              variant="outline"
              nativeButton={false}
              render={<Link to="/login" />}
            >
              {t("auth.backToLogin")}
            </Button>
          </CardContent>
        </Card>
      </AuthShell>
    );
  }

  if (result === "unavailable") {
    return (
      <AuthShell>
        <Card className="w-full max-w-sm gap-6">
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <MailQuestion className="size-5 text-muted-foreground" />
              {t("auth.forgotUnavailableTitle")}
            </CardTitle>
            <CardDescription>{t("auth.forgotUnavailableDesc")}</CardDescription>
          </CardHeader>
          <CardContent>
            <Button
              variant="outline"
              nativeButton={false}
              render={<Link to="/login" />}
            >
              {t("auth.backToLogin")}
            </Button>
          </CardContent>
        </Card>
      </AuthShell>
    );
  }

  return (
    <AuthShell>
      <Card className="w-full max-w-sm gap-6">
        <CardHeader>
          <CardTitle>{t("auth.forgotTitle")}</CardTitle>
          <CardDescription>{t("auth.forgotDesc")}</CardDescription>
        </CardHeader>
        <CardContent>
          <form className="space-y-4" onSubmit={handleSubmit}>
            <div className="space-y-1.5">
              <Label htmlFor="forgot-identifier">
                {t("auth.forgotIdentifier")}
              </Label>
              <Input
                id="forgot-identifier"
                autoComplete="username"
                value={identifier}
                onChange={(e) => setIdentifier(e.target.value)}
                placeholder={t("auth.forgotIdentifierPlaceholder")}
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
              disabled={loading || !identifier.trim()}
            >
              {loading ? (
                <span className="inline-flex items-center gap-2">
                  <span className="size-4 animate-spin rounded-full border-2 border-current border-t-transparent" />
                  {t("auth.forgotSubmitting")}
                </span>
              ) : (
                t("auth.forgotSubmit")
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
