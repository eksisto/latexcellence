import { memo, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import ConfigSection, {
  CheckboxField,
} from "@/components/settings/ConfigSection";

/**
 * 数字输入（所有多用户字段均 ≥ 1）。
 * 用本地草稿态承载自由输入：清空/删除中间态不会立即回弹 1，
 * 仅在失焦时把空/非法值归一化为 1，避免输入体验卡顿（原有实现每次按键立即 ||1）。
 */
function NumberField({ label, value, onChange, hint }) {
  const [draft, setDraft] = useState(() => String(value ?? 1));

  // 外部值变化（如重置/保存后回填）时同步草稿
  useEffect(() => {
    setDraft(String(value ?? 1));
  }, [value]);

  const commit = () => {
    const n = parseInt(draft, 10);
    const safe = Number.isNaN(n) || n < 1 ? 1 : n;
    if (String(safe) !== draft) setDraft(String(safe));
    onChange(safe);
  };

  return (
    <div className="space-y-1">
      <Label className="text-xs">{label}</Label>
      <Input
        type="number"
        min="1"
        value={draft}
        onChange={(e) => {
          const raw = e.target.value;
          setDraft(raw);
          // 输入中仅当是有效正整数才即时提交（合法中间态如 "" 不阻塞输入）
          if (/^\d+$/.test(raw) && Number(raw) >= 1) {
            onChange(Number(raw));
          }
        }}
        onBlur={commit}
      />
      {hint && <p className="text-xs text-muted-foreground">{hint}</p>}
    </div>
  );
}

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
          <div className="grid grid-cols-3 gap-2">
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

          {/* 注册 */}
          <CheckboxField
            label={t("settings.multiUserRegistrationEnabled")}
            checked={multiUser.registration_enabled}
            onChange={(v) => onChange("multi_user.registration_enabled", v)}
          />

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
