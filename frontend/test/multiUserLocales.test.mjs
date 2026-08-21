import test from "node:test";
import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const localesDir = join(
  dirname(fileURLToPath(import.meta.url)),
  "../src/i18n/locales",
);

/** 深度取对象路径（返回 undefined 表示缺失） */
function getPath(obj, path) {
  return path
    .split(".")
    .reduce((acc, key) => (acc == null ? acc : acc[key]), obj);
}

const locales = readdirSync(localesDir)
  .filter((f) => f.endsWith(".json"))
  .map((f) => JSON.parse(readFileSync(join(localesDir, f), "utf8")));

// 后端多用户错误码必须全部进入 errors 命名空间（translateError 依赖）
const ERROR_CODES = [
  "unauthorized",
  "no_permission",
  "invalid_csrf",
  "quota_exceeded",
  "upload_too_large",
  "invalid_archive",
  "archive_unsafe",
  "account_pending",
  "account_disabled",
  "registration_disabled",
  "invalid_username",
  "invalid_password",
  "username_taken",
  "rate_limited",
  "not_found",
  "invalid_request",
  "path_disabled",
  "invalid_workspace",
  "invalid_state",
  "user_not_found",
  "invalid_user",
  "invalid_role",
  "name_conflict",
  "invalid_name",
  "download_failed",
  "transfer_failed",
  "invalid_quota",
];

// P5 页面必需的关键翻译路径
const REQUIRED_PATHS = [
  "common.openProjects",
  "common.role.owner",
  "common.role.editor",
  "common.role.viewer",
  "pages.auth.loginTitle",
  "pages.auth.rememberMe",
  "pages.auth.registerSubmittedTitle",
  "pages.auth.pendingTitle",
  "pages.workbench.title",
  "pages.workbench.tabRecycle",
  "pages.workbench.permissions",
  "pages.workbench.permanentDelete",
  "pages.admin.title",
  "pages.admin.approve",
  "pages.admin.resetPassword",
  "pages.admin.tempPwdTitle",
  "pages.admin.quotaTitle",
  "components.auth.login",
  "components.uploadProject.title",
  "components.permissions.title",
];

test("所有语言含全部分用户错误码与 P5 页面翻译键", () => {
  for (const [i, locale] of locales.entries()) {
    for (const code of ERROR_CODES) {
      assert.ok(
        getPath(locale, `errors.${code}`),
        `locale[${i}] missing errors.${code}`,
      );
    }
    for (const path of REQUIRED_PATHS) {
      assert.ok(getPath(locale, path), `locale[${i}] missing ${path}`);
    }
  }
});
