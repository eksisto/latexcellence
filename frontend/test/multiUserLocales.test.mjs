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

// 单一事实源：从 errorMessages.js 解析 EXACT_MAP 的全部源键（错误码/英文消息），
// 保证本测试的固定列表不会与映射表漂移（新增/删除错误码时两边必须同步）。
const errorMessagesSrc = readFileSync(
  join(
    dirname(fileURLToPath(import.meta.url)),
    "../src/utils/errorMessages.js",
  ),
  "utf8",
);
const exactMapBlock = errorMessagesSrc.match(
  /const EXACT_MAP = \{([\s\S]*?)\n\};/,
);
if (!exactMapBlock)
  throw new Error("cannot parse EXACT_MAP from errorMessages.js");
// 后端错误码 → errors 命名空间翻译键（camelCase）。
// 单一事实源仍是 errorMessages.js 的 EXACT_MAP：源键为后端错误码，
// 值为 locale 的 errors 翻译键（如 no_permission → noPermission）。
const ERROR_CODE_TO_LOCALE_KEY = Object.fromEntries(
  [...exactMapBlock[1].matchAll(/^\s*(?:"([^"]+)"|(\w+)):\s*"([^"]+)"/gm)].map(
    (m) => [m[1] ?? m[2], m[3]],
  ),
);

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
  "components.uploadProject.dirReadFailed",
  "components.uploadProject.dirReadFailedHint",
  "components.uploadProject.dirPackFailed",
  "components.permissions.title",
];

test("所有语言含全部分用户错误码与 P5 页面翻译键", () => {
  // 绑定：多用户错误码必须已在 errorMessages.js 的 EXACT_MAP 中登记，
  // 防止"映射表加了码但测试列表没加"或反向漂移
  const errorCodeKeySet = new Set(Object.keys(ERROR_CODE_TO_LOCALE_KEY));
  for (const code of ERROR_CODES) {
    assert.ok(
      errorCodeKeySet.has(code),
      `ERROR_CODES 中的 ${code} 未在 errorMessages.js EXACT_MAP 中登记`,
    );
  }
  for (const [i, locale] of locales.entries()) {
    for (const code of ERROR_CODES) {
      const localeKey = ERROR_CODE_TO_LOCALE_KEY[code];
      assert.ok(
        getPath(locale, `errors.${localeKey}`),
        `locale[${i}] missing errors.${localeKey} (${code})`,
      );
    }
    for (const path of REQUIRED_PATHS) {
      assert.ok(getPath(locale, path), `locale[${i}] missing ${path}`);
    }
  }
});
