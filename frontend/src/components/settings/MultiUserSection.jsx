import { memo } from "react";
import { useTranslation } from "react-i18next";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import ConfigSection, {
  CheckboxField,
} from "@/components/settings/ConfigSection";
import NumberField from "@/components/settings/NumberField";

function MultiUserSection({ multiUser, onChange }) {
  const { t } = useTranslation("components");
  return (
    <ConfigSection title={t("settings.multiUser")}>
      <CheckboxField
        label={t("settings.multiUserEnabled")}
        checked={multiUser.enabled}
        onChange={(v) => onChange("multi_user.enabled", v)}
      />
      <p className="text-xs text-muted-foreground">
        {t("settings.multiUserEnabledHint")}
      </p>
      <p className="text-xs text-muted-foreground">
        {t("settings.multiUserRestartHint")}
      </p>

      {multiUser.enabled && (
        <div className="ml-6 space-y-3 rounded-md border p-2.5 bg-muted/40">
          {/* 存储路径 */}
          <div className="grid grid-cols-1 gap-2">
            <div className="space-y-1">
              <Label className="text-xs">{t("settings.multiUserDbPath")}</Label>
              <Input
                value={multiUser.db_path}
                onChange={(e) => onChange("multi_user.db_path", e.target.value)}
              />
            </div>
            <div className="space-y-1">
              <Label className="text-xs">
                {t("settings.multiUserUsersRoot")}
              </Label>
              <Input
                value={multiUser.users_root}
                onChange={(e) =>
                  onChange("multi_user.users_root", e.target.value)
                }
              />
            </div>
            <div className="space-y-1">
              <Label className="text-xs">
                {t("settings.multiUserStagingRoot")}
              </Label>
              <Input
                value={multiUser.staging_root}
                onChange={(e) =>
                  onChange("multi_user.staging_root", e.target.value)
                }
              />
            </div>
          </div>

          {/* 配额 */}
          <div className="grid grid-cols-2 gap-2">
            <NumberField
              label={t("settings.multiUserDefaultProjectQuota")}
              value={multiUser.default_project_quota_mb}
              onChange={(v) =>
                onChange("multi_user.default_project_quota_mb", v)
              }
            />
            <NumberField
              label={t("settings.multiUserDefaultDerivedQuota")}
              value={multiUser.default_derived_quota_mb}
              onChange={(v) =>
                onChange("multi_user.default_derived_quota_mb", v)
              }
            />
          </div>

          {/* 上传限制 */}
          <div className="grid grid-cols-2 gap-2">
            <NumberField
              label={t("settings.multiUserMaxUploadArchive")}
              value={multiUser.max_upload_archive_mb}
              onChange={(v) => onChange("multi_user.max_upload_archive_mb", v)}
            />
            <NumberField
              label={t("settings.multiUserMaxUploadFileSize")}
              value={multiUser.max_upload_file_size_mb}
              onChange={(v) =>
                onChange("multi_user.max_upload_file_size_mb", v)
              }
            />
            <NumberField
              label={t("settings.multiUserMaxUploadFiles")}
              value={multiUser.max_upload_files}
              onChange={(v) => onChange("multi_user.max_upload_files", v)}
            />
            <NumberField
              label={t("settings.multiUserMaxCompressionRatio")}
              value={multiUser.max_compression_ratio}
              onChange={(v) => onChange("multi_user.max_compression_ratio", v)}
            />
          </div>

          {/* 生命周期 */}
          <div className="grid grid-cols-2 gap-2">
            <NumberField
              label={t("settings.multiUserRecycleBinDays")}
              value={multiUser.recycle_bin_days}
              onChange={(v) => onChange("multi_user.recycle_bin_days", v)}
            />
            <NumberField
              label={t("settings.multiUserSessionTtl")}
              value={multiUser.session_ttl_days}
              onChange={(v) => onChange("multi_user.session_ttl_days", v)}
            />
            <NumberField
              label={t("settings.multiUserRememberTtl")}
              value={multiUser.remember_ttl_days}
              onChange={(v) => onChange("multi_user.remember_ttl_days", v)}
            />
            <NumberField
              label={t("settings.multiUserWritingChatRetentionDays")}
              hint={t("settings.multiUserWritingChatRetentionHint")}
              min={0}
              value={multiUser.writing_chat_retention_days}
              onChange={(v) =>
                onChange("multi_user.writing_chat_retention_days", v)
              }
            />
          </div>

          {/* LLM 治理 */}
          <div className="grid grid-cols-3 gap-2">
            <NumberField
              label={t("settings.multiUserLlmRateLimit")}
              value={multiUser.llm_rate_limit_per_minute}
              onChange={(v) =>
                onChange("multi_user.llm_rate_limit_per_minute", v)
              }
            />
            <NumberField
              label={t("settings.multiUserLlmMaxConcurrency")}
              value={multiUser.llm_max_concurrency_per_user}
              onChange={(v) =>
                onChange("multi_user.llm_max_concurrency_per_user", v)
              }
            />
            <NumberField
              label={t("settings.multiUserLoginRateLimit")}
              value={multiUser.login_rate_limit_per_minute}
              onChange={(v) =>
                onChange("multi_user.login_rate_limit_per_minute", v)
              }
            />
          </div>

          {/* 编辑日志（反向补丁撤销） */}
          <div className="grid grid-cols-3 gap-2">
            <NumberField
              label={t("settings.multiUserEditLogMaxOps")}
              value={multiUser.edit_log_max_ops}
              onChange={(v) => onChange("multi_user.edit_log_max_ops", v)}
            />
            <NumberField
              label={t("settings.multiUserEditLogMaxOpMb")}
              value={multiUser.edit_log_max_op_mb}
              onChange={(v) => onChange("multi_user.edit_log_max_op_mb", v)}
            />
            <NumberField
              label={t("settings.multiUserEditLogTtlHours")}
              value={multiUser.edit_log_ttl_hours}
              onChange={(v) => onChange("multi_user.edit_log_ttl_hours", v)}
            />
          </div>

          {/* 分片上传（大文件断点续传） */}
          <div className="grid grid-cols-2 gap-2">
            <NumberField
              label={t("settings.multiUserUploadSessionTtlHours")}
              hint={t("settings.multiUserUploadSessionTtlHint")}
              value={multiUser.upload_session_ttl_hours}
              onChange={(v) =>
                onChange("multi_user.upload_session_ttl_hours", v)
              }
            />
            <NumberField
              label={t("settings.multiUserChunkPartMaxMb")}
              hint={t("settings.multiUserChunkPartMaxMbHint")}
              value={multiUser.chunk_part_max_mb}
              onChange={(v) => onChange("multi_user.chunk_part_max_mb", v)}
            />
          </div>

          {/* 注册 */}
          <CheckboxField
            label={t("settings.multiUserRegistrationEnabled")}
            checked={multiUser.registration_enabled}
            onChange={(v) => onChange("multi_user.registration_enabled", v)}
          />

          {/* 注册自动审批（IP 策略） */}
          <CheckboxField
            label={t("settings.multiUserAutoApprove")}
            checked={multiUser.auto_approve_enabled}
            onChange={(v) => onChange("multi_user.auto_approve_enabled", v)}
          />
          <p className="text-xs text-muted-foreground">
            {t("settings.multiUserAutoApproveHint")}
          </p>
          {multiUser.auto_approve_enabled && (
            <div className="ml-6 space-y-3 rounded-md border p-2.5 bg-muted/40">
              <div className="grid grid-cols-2 gap-2">
                <NumberField
                  label={t("settings.multiUserAutoApproveMaxPerIp")}
                  hint={t("settings.multiUserAutoApproveMaxPerIpHint")}
                  min={1}
                  value={multiUser.auto_approve_max_accounts_per_ip}
                  onChange={(v) =>
                    onChange("multi_user.auto_approve_max_accounts_per_ip", v)
                  }
                />
                <NumberField
                  label={t("settings.multiUserRegisterRateLimit")}
                  min={1}
                  value={multiUser.register_rate_limit_per_hour}
                  onChange={(v) =>
                    onChange("multi_user.register_rate_limit_per_hour", v)
                  }
                />
              </div>
              <div className="space-y-1">
                <Label className="text-xs">
                  {t("settings.multiUserAutoApproveCidrs")}
                </Label>
                <Input
                  placeholder="59.77.0.0/16, 192.168.1.5"
                  value={(multiUser.auto_approve_allowed_cidrs || []).join(
                    ", ",
                  )}
                  onChange={(e) =>
                    onChange(
                      "multi_user.auto_approve_allowed_cidrs",
                      e.target.value
                        .split(",")
                        .map((s) => s.trim())
                        .filter(Boolean),
                    )
                  }
                />
                <p className="text-xs text-muted-foreground">
                  {t("settings.multiUserAutoApproveCidrsHint")}
                </p>
              </div>
              <div className="grid grid-cols-1 gap-2">
                <div className="space-y-1">
                  <Label className="text-xs">
                    {t("settings.multiUserGeoDbPath")}
                  </Label>
                  <Input
                    placeholder="data/GeoLite2-City.mmdb"
                    value={multiUser.auto_approve_geo_db_path}
                    onChange={(e) =>
                      onChange(
                        "multi_user.auto_approve_geo_db_path",
                        e.target.value,
                      )
                    }
                  />
                  <p className="text-xs text-muted-foreground">
                    {t("settings.multiUserGeoDbPathHint")}
                  </p>
                </div>
              </div>
              <div className="grid grid-cols-2 gap-2">
                <div className="space-y-1">
                  <Label className="text-xs">
                    {t("settings.multiUserGeoCountries")}
                  </Label>
                  <Input
                    placeholder="CN"
                    value={(
                      multiUser.auto_approve_allowed_countries || []
                    ).join(", ")}
                    onChange={(e) =>
                      onChange(
                        "multi_user.auto_approve_allowed_countries",
                        e.target.value
                          .split(",")
                          .map((s) => s.trim())
                          .filter(Boolean),
                      )
                    }
                  />
                </div>
                <div className="space-y-1">
                  <Label className="text-xs">
                    {t("settings.multiUserGeoCities")}
                  </Label>
                  <Input
                    placeholder="合肥, Hefei"
                    value={(multiUser.auto_approve_allowed_cities || []).join(
                      ", ",
                    )}
                    onChange={(e) =>
                      onChange(
                        "multi_user.auto_approve_allowed_cities",
                        e.target.value
                          .split(",")
                          .map((s) => s.trim())
                          .filter(Boolean),
                      )
                    }
                  />
                </div>
              </div>
              <p className="text-xs text-muted-foreground">
                {t("settings.multiUserGeoListHint")}
              </p>
            </div>
          )}

          {/* 真实客户端 IP 解析（反代部署） */}
          <div className="space-y-1">
            <Label className="text-xs">
              {t("settings.multiUserTrustedProxies")}
            </Label>
            <Input
              placeholder="127.0.0.1, 173.245.48.0/20"
              value={(multiUser.trusted_proxies || []).join(", ")}
              onChange={(e) =>
                onChange(
                  "multi_user.trusted_proxies",
                  e.target.value
                    .split(",")
                    .map((s) => s.trim())
                    .filter(Boolean),
                )
              }
            />
            <p className="text-xs text-muted-foreground">
              {t("settings.multiUserTrustedProxiesHint")}
            </p>
          </div>

          {/* 管理员引导（环境变量名） */}
          <div className="grid grid-cols-2 gap-2">
            <div className="space-y-1">
              <Label className="text-xs">
                {t("settings.multiUserAdminUsernameEnv")}
              </Label>
              <Input
                value={multiUser.admin_username_env}
                onChange={(e) =>
                  onChange("multi_user.admin_username_env", e.target.value)
                }
              />
            </div>
            <div className="space-y-1">
              <Label className="text-xs">
                {t("settings.multiUserAdminPasswordEnv")}
              </Label>
              <Input
                value={multiUser.admin_password_env}
                onChange={(e) =>
                  onChange("multi_user.admin_password_env", e.target.value)
                }
              />
            </div>
          </div>
        </div>
      )}
    </ConfigSection>
  );
}

export default memo(MultiUserSection);
