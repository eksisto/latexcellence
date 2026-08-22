import { memo } from "react";
import { useTranslation } from "react-i18next";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import ConfigSection, {
  CheckboxField,
} from "@/components/settings/ConfigSection";

/**
 * SMTP 邮件通知设置分区（多用户 v2 P6，管理员）。
 * enabled=false 或 host 为空时全部通知静默跳过、登录页隐藏找回入口。
 * password 沿用 *** 掩码协议（后端返回掩码值，原样回传即保留原密码）。
 */
const ENCRYPTION_HINT_KEY = {
  ssl: "settings.smtpEncryptionSslHint",
  starttls: "settings.smtpEncryptionStarttlsHint",
  none: "settings.smtpEncryptionNoneHint",
};

function SmtpSection({ smtp, onChange }) {
  const { t } = useTranslation("components");
  return (
    <ConfigSection title={t("settings.smtp")}>
      <CheckboxField
        label={t("settings.smtpEnabled")}
        checked={smtp.enabled}
        onChange={(v) => onChange("smtp.enabled", v)}
      />
      <p className="text-xs text-muted-foreground">{t("settings.smtpHint")}</p>

      <div className="grid grid-cols-2 gap-2">
        <div className="space-y-1">
          <Label className="text-xs">{t("settings.smtpHost")}</Label>
          <Input
            value={smtp.host}
            placeholder="smtp.example.com"
            onChange={(e) => onChange("smtp.host", e.target.value)}
          />
        </div>
        <div className="space-y-1">
          <Label className="text-xs">{t("settings.smtpPort")}</Label>
          <Input
            type="number"
            min={1}
            max={65535}
            value={smtp.port}
            onChange={(e) => {
              const n = Number(e.target.value);
              if (Number.isInteger(n) && n >= 1 && n <= 65535) {
                onChange("smtp.port", n);
              }
            }}
          />
        </div>
      </div>

      {/* 加密方式 */}
      <div className="space-y-1">
        <Label className="text-xs">{t("settings.smtpEncryption")}</Label>
        <Select
          value={smtp.encryption}
          onValueChange={(v) => onChange("smtp.encryption", v)}
        >
          <SelectTrigger>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="ssl">
              {t("settings.smtpEncryptionSsl")}
            </SelectItem>
            <SelectItem value="starttls">
              {t("settings.smtpEncryptionStarttls")}
            </SelectItem>
            <SelectItem value="none">
              {t("settings.smtpEncryptionNone")}
            </SelectItem>
          </SelectContent>
        </Select>
        <p className="text-xs text-muted-foreground">
          {t(ENCRYPTION_HINT_KEY[smtp.encryption] ?? ENCRYPTION_HINT_KEY.ssl)}
        </p>
      </div>

      {/* 认证凭据 */}
      <div className="grid grid-cols-2 gap-2">
        <div className="space-y-1">
          <Label className="text-xs">{t("settings.smtpUsername")}</Label>
          <Input
            value={smtp.username}
            autoComplete="off"
            onChange={(e) => onChange("smtp.username", e.target.value)}
          />
        </div>
        <div className="space-y-1">
          <Label className="text-xs">{t("settings.smtpPassword")}</Label>
          <Input
            type="password"
            value={smtp.password || ""}
            autoComplete="new-password"
            onChange={(e) => onChange("smtp.password", e.target.value)}
          />
        </div>
      </div>
      <p className="text-xs text-muted-foreground">
        {t("settings.smtpPasswordHint")}
      </p>

      {/* 发件人 */}
      <div className="grid grid-cols-2 gap-2">
        <div className="space-y-1">
          <Label className="text-xs">{t("settings.smtpFromAddress")}</Label>
          <Input
            value={smtp.from_address}
            placeholder="noreply@example.com"
            onChange={(e) => onChange("smtp.from_address", e.target.value)}
          />
        </div>
        <div className="space-y-1">
          <Label className="text-xs">{t("settings.smtpFromName")}</Label>
          <Input
            value={smtp.from_name}
            onChange={(e) => onChange("smtp.from_name", e.target.value)}
          />
        </div>
      </div>

      {/* 站点地址与模板语言 */}
      <div className="space-y-1">
        <Label className="text-xs">{t("settings.smtpSiteBaseUrl")}</Label>
        <Input
          value={smtp.site_base_url}
          placeholder="https://latexcellence.example.com"
          onChange={(e) => onChange("smtp.site_base_url", e.target.value)}
        />
        <p className="text-xs text-muted-foreground">
          {t("settings.smtpSiteBaseUrlHint")}
        </p>
      </div>
      <div className="grid grid-cols-2 gap-2">
        <div className="space-y-1">
          <Label className="text-xs">
            {t("settings.smtpTemplateLanguage")}
          </Label>
          <Select
            value={smtp.template_language}
            onValueChange={(v) => onChange("smtp.template_language", v)}
          >
            <SelectTrigger>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="zh">
                {t("settings.smtpTemplateLanguageZh")}
              </SelectItem>
              <SelectItem value="en">
                {t("settings.smtpTemplateLanguageEn")}
              </SelectItem>
            </SelectContent>
          </Select>
          <p className="text-xs text-muted-foreground">
            {t("settings.smtpTemplateLanguageHint")}
          </p>
        </div>
        <div className="grid grid-cols-2 content-start gap-2">
          <div className="space-y-1">
            <Label className="text-xs">
              {t("settings.smtpMaxSendPerMinute")}
            </Label>
            <Input
              type="number"
              min={1}
              value={smtp.max_send_per_minute}
              onChange={(e) => {
                const n = Number(e.target.value);
                if (Number.isInteger(n) && n >= 1) {
                  onChange("smtp.max_send_per_minute", n);
                }
              }}
            />
          </div>
          <div className="space-y-1">
            <Label className="text-xs">{t("settings.smtpRetryAttempts")}</Label>
            <Input
              type="number"
              min={1}
              value={smtp.retry_attempts}
              onChange={(e) => {
                const n = Number(e.target.value);
                if (Number.isInteger(n) && n >= 1) {
                  onChange("smtp.retry_attempts", n);
                }
              }}
            />
          </div>
        </div>
      </div>
    </ConfigSection>
  );
}

export default memo(SmtpSection);
