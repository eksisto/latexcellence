/**
 * 后端错误消息 → 用户语言翻译模块
 *
 * 设计说明：
 * - 后端 HTTPException 返回的 detail 字段多为英文
 * - 本模块根据 i18n 当前语言翻译错误消息
 * - 无法匹配时原样返回
 * - 模式匹配核心（PATTERN_MAP / matchPattern）抽离在 ./errorPatterns.js
 *   纯模块中，便于 node --test 直接单测；EXACT_MAP 必须留在本文件——
 *   frontend/test/multiUserLocales.test.mjs 以源码文本解析它，作为三语
 *   错误码覆盖校验的单一事实源
 */

import i18n from "@/i18n";

import { matchPattern } from "./errorPatterns";

// ── 静态消息（精确匹配）──────────────────────────────────────────
const EXACT_MAP = {
  "rate limited": "webRateLimited",
  "challenge required": "webChallengeRequired",
  "captcha required": "webCaptchaRequired",
  "javascript required": "webJavascriptRequired",
  "access controlled": "webAccessControlled",
  "paywall detected": "webPaywallDetected",
  "browser unavailable": "webBrowserUnavailable",
  "circuit open": "webCircuitOpen",
  // 网络 / 服务不可达
  "Failed to fetch": "fetchFailed",
  "NetworkError when attempting to fetch resource.": "fetchFailed",
  "Load failed": "fetchFailed",
  "Request timed out": "requestTimedOut",
  "Internal Server Error": "internalServerError",
  "Server configuration not loaded": "configNotLoaded",
  "Access token required or invalid": "accessTokenRequired",
  "Origin not allowed by CORS policy": "originNotAllowed",

  // 多用户模式（P1–P5）错误码（后端 detail.error）
  unauthorized: "unauthorized",
  invalid_credentials: "invalidCredentials",
  no_permission: "noPermission",
  invalid_csrf: "invalidCsrf",
  quota_exceeded: "quotaExceeded",
  upload_too_large: "uploadTooLarge",
  invalid_archive: "invalidArchive",
  archive_unsafe: "archiveUnsafe",
  rar_extractor_missing: "rarExtractorMissing",
  account_pending: "accountPending",
  account_disabled: "accountDisabled",
  registration_disabled: "registrationDisabled",
  invalid_username: "invalidUsername",
  invalid_password: "invalidPassword",
  username_taken: "usernameTaken",
  rate_limited: "rateLimited",
  // 注册自动审批（IP 策略）错误码
  register_rate_limited: "registerRateLimited",
  not_found: "notFound",
  invalid_request: "invalidRequest",
  path_disabled: "pathDisabled",
  project_unavailable: "projectUnavailable",
  invalid_workspace: "invalidWorkspace",
  invalid_state: "invalidState",
  user_not_found: "userNotFound",
  invalid_user: "invalidUser",
  invalid_role: "invalidRole",
  name_conflict: "nameConflict",
  invalid_name: "invalidName",
  download_failed: "downloadFailed",
  transfer_failed: "transferFailed",
  invalid_quota: "invalidQuota",

  // Git 版本管理（git_manage）错误码
  git_history_too_large: "gitHistoryTooLarge",
  git_already_initialized: "gitAlreadyInitialized",
  git_no_changes: "gitNoChanges",
  git_repository_locked: "gitRepositoryLocked",
  git_message_required: "gitMessageRequired",

  // 编译沙箱（多用户 v2 P1a）错误码
  sandbox_unavailable: "sandboxUnavailable",
  sandbox_busy: "sandboxBusy",
  sandbox_killed_oom: "sandboxKilledOom",

  // LLM 每用户治理（多用户 v2 P1b）错误码
  llm_rate_limited: "llmRateLimited",
  llm_concurrency_limited: "llmConcurrencyLimited",

  // 模型连通性测试（deploy-followups-plan 阶段四）内联错误码
  // （POST /api/llm/test 响应体 error.code，消费处手动 translateError）
  api_key_missing: "llmTestApiKeyMissing",
  llm_test_timeout: "llmTestTimeout",
  llm_test_upstream: "llmTestUpstream",
  llm_test_unreachable: "llmTestUnreachable",
  llm_test_failed: "llmTestFailed",

  // 反向补丁撤销（多用户 v2 P3）错误码
  quota_exceeded_recover: "quotaExceededRecover",
  op_not_undoable: "opNotUndoable",

  // 衍生数据记账与自助清理（多用户 v2 P4）错误码
  invalid_category: "invalidCategory",

  // 模板库（/api/templates，template-plan 阶段②）错误码
  invalid_id: "invalidId",
  builtin_readonly: "builtinReadonly",
  no_main_file: "noMainFile",
  invalid_manifest: "invalidManifest",
  not_viewable: "notViewable",
  read_failed: "readFailed",
  invalid_file_type: "invalidFileType",
  target_dir_required: "targetDirRequired",
  target_not_empty: "targetNotEmpty",
  invalid_session: "invalidSession",

  // 分片上传与断点续传（多用户 v2 P5）错误码
  upload_session_not_found: "uploadSessionNotFound",
  upload_session_expired: "uploadSessionExpired",
  part_md5_mismatch: "partMd5Mismatch",
  part_missing: "partMissing",

  // SMTP 邮件与自助找回（多用户 v2 P6）错误码
  email_not_available: "emailNotAvailable",
  reset_token_invalid: "resetTokenInvalid",
  email_taken: "emailTaken",
  invalid_email: "invalidEmail",

  // 项目管理
  "No project opened": "noProjectOpened",

  // LLM / API Key
  "No API key configured": "noApiKeyConfigured",
  "Max retries exceeded": "maxRetriesExceeded",
  "Style modify agent is disabled": "styleModifyAgentDisabled",
  "No proposal to refine": "noProposalToRefine",
  "No proposal for this session": "noProposalForSession",
  "Proposal revision does not match the latest proposal":
    "proposalRevisionMismatch",
  "Only the latest proposal revision can be applied": "latestRevisionRequired",
  "Session is already applying a proposal": "sessionAlreadyApplying",
  "No staged changes to apply": "noStagedChanges",
  "Session belongs to a different workspace": "sessionDifferentWorkspace",
  "Project root has changed since the proposal was created":
    "projectRootChanged",
  "Project root has changed since the operation was applied":
    "projectRootChangedOperation",
  "Project fingerprint mismatch": "projectFingerprintMismatch",
  "Candidate content must not be empty": "candidateContentEmpty",
  "query must not be empty": "queryEmpty",
  "Operation belongs to a different workspace": "operationDifferentWorkspace",
  "Operation is not in applied state": "operationNotApplied",
  "Cannot delete a session that is running or applying":
    "sessionRunningDeleteDenied",

  // 段落应用错误
  "Marker map missing": "markerMapMissing",
  file_conflict: "fileConflict",

  // 编辑器整文件读写（backend/modules/editor，editor-plan 阶段一接口）
  invalid_content: "invalidContent",
  invalid_path: "invalidPath",
  file_not_found: "fileNotFound",
  invalid_encoding: "invalidEncoding",
  // 请求体长度上限（save-file content / 编辑器助手 buffer 等）
  content_too_long: "contentTooLong",
  // 编辑器一键格式化（backend/modules/editor，editor-productivity-plan 阶段五）
  format_failed: "formatFailed",

  // SSE 流式兜底：未预期异常收敛为稳定错误码，不向客户端透传 str(e)
  internal_error: "internalError",

  // 编辑器功能开关把关（editor-plan 阶段四 / 决策 Q26）
  editor_disabled: "editorDisabled",
  assistant_disabled: "assistantDisabled",

  // 参数校验
  "Empty reference example": "emptyReferenceExample",
  "Empty metadata": "emptyMetadata",
  "Empty BibTeX code": "emptyBibtexCode",
  "Empty DOI": "emptyDoi",
  "DOI is required": "doiRequired",
  "Empty image files": "emptyImageFiles",
  "Either text or image must be provided": "eitherTextOrImageRequired",
  "Empty image file": "emptyImageFile",
  "No file selected": "noFileSelected",
  "Empty file": "emptyFile",
  "Format name required": "formatNameRequired",
  "LaTeX code cannot be empty": "latexCodeEmpty",
  "Description cannot be empty": "descriptionEmpty",
  "Markdown text is empty": "markdownEmpty",
  "Input text is empty": "inputEmpty",
  "Uploaded file is empty": "emptyFile",
  "Source and target formats must be different": "formatsMustDiffer",
  "Target paragraph is empty": "targetParagraphEmpty",
  "Message not found": "messageNotFound",
  "Session not found": "sessionNotFound",
  "Diff PDF not found": "diffPdfNotFound",
  "No trace recorded for this session": "noTraceRecorded",
  // 模板生成产物预览（PDF）
  "Preview not supported for this file type": "previewUnsupportedFileType",
  "Preview file not found": "previewFileNotFound",
  "Data profile is missing": "plotGenDataProfileMissing",
  "Session meta not found": "plotGenSessionMetaNotFound",
  // plot_gen Agent 收尾阶段 warning（backend/modules/plot_gen/agent.py）
  "Reached maximum rounds without successful TikZ compilation.":
    "plotGenMaxRoundsNoTikz",
  "Agent called finish without a successful TikZ compilation. The TikZ artifact may be missing or invalid.":
    "plotGenFinishWithoutTikz",
  "WARNING: `finish` accepted although figure-affecting files changed since the last successful compile (override after a block); the artifacts may be stale.":
    "plotGenStaleFinishAccepted",
  "No files in sandbox": "plotGenNoFilesInSandbox",

  // plot_gen Agent 止损指令（同时作为给 LLM 的编排消息，展示于 warning 条）
  "VERIFIED-LOOP BLOCK: `compile_tikz` already succeeded and no figure-affecting file was modified since. Do not call `read_file`/`list_files`/`python_sandbox`/`compile_tikz` again; call `finish` now.":
    "plotGenVerifiedLoopBlock",
  "STALE FINISH BLOCKED: figure-affecting files were modified or `python_sandbox` re-ran since the last successful `compile_tikz`, so the artifacts may no longer match the current source. Rerun `python_sandbox` and `compile_tikz`, then call `finish` again. If you really cannot compile, call `finish` once more to override with a warning.":
    "plotGenStaleFinishBlocked",

  // style_modify / template_gen Agent 收尾阶段 warning
  "Agent finished while the last shadow compilation failed; review the diff before applying.":
    "styleModifyFinishCompileFailed",
  "Agent finished without running a shadow compilation; the proposal may not be verified.":
    "styleModifyFinishNoCompile",
  "Agent called finish, but the last compilation still failed. The project may not build successfully. Please check the sandbox files manually.":
    "templateGenFinishCompileFailed",

  // 写作聊天工具确认 / 编辑器助手
  "Pending tool not found or already resolved": "pendingToolNotFound",
  "Clarification request not found or already resolved":
    "clarificationNotFound",
  "No API key configured — terminology clustering requires LLM":
    "noApiKeyTerminologyClustering",

  // 通用 / 多用户基础设施
  "Not found": "notFoundGeneric",
  "path is required": "pathRequired",
  "session_id cannot be empty": "plotGenSessionIdEmpty",
  "session_id contains invalid characters": "plotGenSessionIdInvalid",
  "Invalid job id": "invalidJobId",
  "Missing content type": "missingContentType",
  "Main file must be a .tex file": "mainFileMustBeTex",
  "Git revision must not be empty or padded with spaces": "gitRevisionEmpty",
  "Invalid project-relative path": "invalidProjectRelativePath",
  "Project directory not found in target revision":
    "projectDirNotFoundInRevision",
  "Opened project is not inside its Git repository": "gitDiffProjectNotInRepo",
  "Diff PDF generated but compilation failed. Please refer to the raw output to diagnose the issue.":
    "diffCompileFailedFallback",
  "LLM error translation failed, please refer to the raw compilation output below":
    "llmTranslationFailed",
  "Pandoc produced empty output": "pandocEmptyOutput",
  "Command timed out": "agentCommandTimedOut",
  "file_path must be a string": "agentFilePathMustBeString",
  "content must be a string": "agentContentMustBeString",

  // Pandoc
  "Pandoc is not installed. Install it from https://pandoc.org/installing.html":
    "pandocNotInstalled",
};

/**
 * 将后端错误消息翻译为当前语言
 * @param {string|object} msg 原始错误消息（或结构化 detail：{ error, message, ... }）
 * @returns {string} 翻译后的错误消息（无法匹配时原样返回）
 */
export function translateError(msg) {
  if (!msg) return i18n.t("errors:unknown");

  // 结构化后端错误：detail = { error: code, message: text }
  // 优先按错误码精确翻译（如 file_conflict / no_permission），
  // 错误码未收录时回落到 message 文本（可能为英文，走精确/模式匹配）。
  // 命中映射表时，detail 中的额外字段（如 LLM 治理的 retry_after）会作为
  // i18n 插值参数传入，支持带 {{占位符}} 的错误文案。
  if (msg && typeof msg === "object") {
    const code = msg.error;
    const message = msg.message || "";
    if (code && typeof code === "string") {
      const key = EXACT_MAP[code];
      if (key !== undefined) {
        const params = { ...msg };
        delete params.error;
        delete params.message;
        return i18n.t(`errors:${key}`, params);
      }
      const translated = translateError(code);
      // 码未收录但恰好命中精确/模式表时（译文 ≠ 码字符串）视为翻译成功
      if (translated !== code) {
        return translated;
      }
    }
    return translateError(message);
  }

  // 1. 精确匹配
  if (msg in EXACT_MAP) {
    return i18n.t(`errors:${EXACT_MAP[msg]}`);
  }

  // 1.5 逐段检查前缀（backend/core/base_paragraph_check.py 的
  // "{file} P{n}: {子消息}"）：剥前缀翻译子消息后拼回，使精确键与
  // 有锚定模式也能命中；子消息未命中则继续走原文模式匹配
  const prefixMatch = /^(\S+ P\d+: )([\s\S]+)$/.exec(msg);
  if (prefixMatch) {
    const translatedSub = translateError(prefixMatch[2]);
    if (translatedSub !== prefixMatch[2]) {
      return prefixMatch[1] + translatedSub;
    }
  }

  // 2. 模式匹配（表与匹配核心见 ./errorPatterns.js，含去 ^ 锚定的
  // LLM 治理模式，可命中 "{文件名} P{段号}: " 前缀的消息）
  const matched = matchPattern(msg);
  if (matched) {
    return i18n.t(`errors:${matched.key}`, matched.params);
  }

  // 3. 无法匹配则原样返回
  return msg;
}
