/**
 * 后端错误消息 → 用户语言翻译模块
 *
 * 设计说明：
 * - 后端 HTTPException 返回的 detail 字段多为英文
 * - 本模块根据 i18n 当前语言翻译错误消息
 * - 无法匹配时原样返回
 */

import i18n from "@/i18n";

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
  account_pending: "accountPending",
  account_disabled: "accountDisabled",
  registration_disabled: "registrationDisabled",
  invalid_username: "invalidUsername",
  invalid_password: "invalidPassword",
  username_taken: "usernameTaken",
  rate_limited: "rateLimited",
  not_found: "notFound",
  invalid_request: "invalidRequest",
  path_disabled: "pathDisabled",
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

  // 编译沙箱（多用户 v2 P1a）错误码
  sandbox_unavailable: "sandboxUnavailable",
  sandbox_busy: "sandboxBusy",
  sandbox_killed_oom: "sandboxKilledOom",

  // LLM 每用户治理（多用户 v2 P1b）错误码
  llm_rate_limited: "llmRateLimited",
  llm_concurrency_limited: "llmConcurrencyLimited",

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

  // 参数校验
  "Empty reference example": "emptyReferenceExample",
  "Empty metadata": "emptyMetadata",
  "Empty BibTeX code": "emptyBibtexCode",
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
  "Data profile is missing": "plotGenDataProfileMissing",
  "Session meta not found": "plotGenSessionMetaNotFound",
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

// ── 模式消息（正则匹配，提取参数后用 i18n.t() 翻译）────────────────
const PATTERN_MAP = [
  // 网络 / 服务不可达（跨浏览器变体）
  [
    /^NetworkError when attempting to fetch resource/,
    "fetchFailed",
    () => ({}),
  ],
  [/^Load failed/, "fetchFailed", () => ({})],

  // SSE 流解析失败（JSON.parse 的引擎差异消息）
  [/^Unexpected (token|end of JSON input)/, "sseParseFailed", () => ({})],
  [/^JSON\.parse:/, "sseParseFailed", () => ({})],
  [/^JSON Parse error:/, "sseParseFailed", () => ({})],

  // 超时
  [
    /^Compilation timed out after (\d+) seconds/,
    "compilationTimeout",
    (m) => ({ seconds: m[1] }),
  ],
  [
    /^Command timed out after (\d+) seconds/,
    "commandTimeout",
    (m) => ({ seconds: m[1] }),
  ],
  [/timed out/, "timeout", () => ({})],

  // LLM 每用户治理（多用户 v2 P1b）：SSE error 事件携带英文原文时按模式翻译；
  // 占位符与后端 detail.retry_after 字段同名，结构化路径直接透传即可插值
  [
    /^LLM rate limit exceeded \(\d+\/\d+ per \d+s\); retry after (\d+)s/,
    "llmRateLimited",
    (m) => ({ retry_after: m[1] }),
  ],
  [
    /^LLM concurrency limit reached \(\d+\/\d+\); retry after (\d+)s/,
    "llmConcurrencyLimited",
    (m) => ({ retry_after: m[1] }),
  ],

  // 项目管理
  [
    /^Project path not found:\s*(.+)/,
    "projectPathNotFound",
    (m) => ({ path: m[1] }),
  ],
  [/^Not a directory:\s*(.+)/, "notADirectory", (m) => ({ path: m[1] })],
  [/^File not found:\s*(.+)/, "fileNotFound", (m) => ({ path: m[1] })],
  [/^Main file not found:\s*(.+)/, "mainFileNotFound", (m) => ({ path: m[1] })],

  // 会话 / 提示词 / 格式
  [
    /^Session not found:\s*(.+)/,
    "sessionNotFoundDetail",
    (m) => ({ name: m[1] }),
  ],
  [/^Prompt not found:\s*(.+)/, "promptNotFound", (m) => ({ name: m[1] })],
  [/^Format not found:\s*(.+)/, "formatNotFound", (m) => ({ name: m[1] })],

  // Git
  [/^fatal: not a git repository/, "gitNotRepository", () => ({})],
  [/^fatal: 不是 git 仓库/, "gitNotRepository", () => ({})],
  [
    /^Invalid Git revision:\s*(.+)/,
    "invalidGitRevision",
    (m) => ({ revision: m[1] }),
  ],
  [/^latexdiff command not found/, "latexdiffNotInstalled", () => ({})],
  [/^latexdiff failed/, "latexdiffFailed", () => ({})],
  [
    /^File not found in revision (.+?):\s*(.+)/,
    "fileNotFoundInRevision",
    (m) => ({ revision: m[1], path: m[2] }),
  ],

  // 段落索引
  [
    /^Paragraph index (\d+) out of range \(0-(\d+)\)/,
    "paragraphIndexOutOfRange",
    (m) => ({ index: m[1], max: m[2] }),
  ],

  // 图片
  [
    /^Unsupported image format:\s*(.+?)\.?\s*Supported:/,
    "unsupportedImageFormat",
    (m) => ({ format: m[1] }),
  ],
  [
    /^Image too large:\s*([\d.]+)MB\. Maximum:\s*([\d.]+)MB/,
    "imageTooLarge",
    (m) => ({ size: m[1], max: m[2] }),
  ],

  // 配置校验
  [
    /^Config validation failed:\s*(.+)/,
    "configValidationFailed",
    (m) => ({ details: m[1] }),
  ],

  // 科研绘图
  [
    /^Scientific plotting failed:\s*(.+)/,
    "plotGenGenerationFailed",
    (m) => ({ error: m[1] }),
  ],
  [
    /^Data rows exceed the limit:\s*(\d+) > (\d+)/,
    "plotGenDataRowsExceeded",
    (m) => ({ rows: m[1], max: m[2] }),
  ],
  [/^Failed to decode CSV/, "plotGenCsvDecodeFailed", () => ({})],

  // 文件上传
  [
    /^File too large:\s*([\d.]+)MB\. Maximum:\s*([\d.]+)MB/,
    "fileTooLarge",
    (m) => ({ size: m[1], max: m[2] }),
  ],
  [
    /^Unsupported file type:\s*'(.+?)'/,
    "unsupportedFileType",
    (m) => ({ type: m[1] }),
  ],

  // 文件修改冲突
  [
    /^File (.+) has been modified since parsing/,
    "fileModifiedSinceParsing",
    (m) => ({ file: m[1] }),
  ],
  [
    /^File (.+) has been modified since the proposal was created/,
    "fileModifiedSinceProposal",
    (m) => ({ file: m[1] }),
  ],
  [
    /^File (.+) was modified after it was applied/,
    "fileModifiedAfterApply",
    (m) => ({ file: m[1] }),
  ],
  [
    /^Candidate file missing: (.+)/,
    "candidateFileMissing",
    (m) => ({ file: m[1] }),
  ],
  [/^Undo backup missing: (.+)/, "undoBackupMissing", (m) => ({ file: m[1] })],
  [/^Operation not found: (.+)/, "operationNotFound", (m) => ({ name: m[1] })],
  [
    /^File (.+) exceeds maximum size of (\d+) KB/,
    "fileTooLargeKb",
    (m) => ({ file: m[1], kb: m[2] }),
  ],
  [
    /^Session candidate size exceeds (\d+) KB/,
    "sessionCandidateTooLarge",
    (m) => ({ kb: m[1] }),
  ],
  [
    /^(?:start_line|line_range)(?: start)? (?:out of range|must be less than)|^line_range must be a two-item array|^read_file line_range must satisfy/,
    "lineRangeInvalid",
    () => ({}),
  ],
  [
    /^Tool call limit (\d+) exceeded/,
    "toolCallLimitExceeded",
    (m) => ({ limit: m[1] }),
  ],
  [
    /^Shadow compile limit (\d+) reached/,
    "compileLimitReached",
    (m) => ({ limit: m[1] }),
  ],

  // style_modify Agent 编译完整性
  [
    /^Finish refused: the staged file set changed after the last shadow compilation \(affected: (.+?)\)/,
    "finishRefusedStale",
    (m) => ({ files: m[1] }),
  ],
  [
    /^Reached maximum rounds with the staged file set changed after the last shadow compilation \(affected: (.+?)\); the auto-generated proposal/,
    "maxRoundsStaleProposal",
    (m) => ({ files: m[1] }),
  ],

  // Agent 私有文件工具 / 沙箱执行失败
  [
    /^Path validation failed:\s*(.+)/,
    "agentPathValidationFailed",
    (m) => ({ detail: m[1] }),
  ],
  [
    /^file_path (?:must not|must|contains|resolves)/,
    "agentInvalidFilePath",
    () => ({}),
  ],
  [
    /^Maximum (?:distinct-)?file count (\d+) reached/,
    "agentMaxFileCountReached",
    (m) => ({ count: m[1] }),
  ],
  [
    /^Cannot write to protected path:\s*(.+)/,
    "agentProtectedPath",
    (m) => ({ path: m[1] }),
  ],
  [
    /^Python syntax error:\s*(.+)/,
    "agentPythonSyntaxError",
    (m) => ({ detail: m[1] }),
  ],
  [/^File too large:\s*(.+)/, "agentFileTooLarge", (m) => ({ file: m[1] })],
  [
    /^File (.+) was rejected:\s*(.+)/,
    "agentFileRejected",
    (m) => ({ file: m[1], detail: m[2] }),
  ],
  [
    /^Script not found:\s*(.+)/,
    "agentScriptNotFound",
    (m) => ({ script: m[1] }),
  ],
  [
    /^TikZ compilation is disabled by config/,
    "agentTikzCompileDisabled",
    () => ({}),
  ],
  [
    /^TikZ file not found:\s*(.+?)(?:\.\s|$)/,
    "agentTikzFileNotFound",
    (m) => ({ file: m[1] }),
  ],
  [/^TikZ PRECHECK failed; /, "agentTikzPrecheckFailed", () => ({})],
  [/^Execution failed; /, "agentExecutionFailed", () => ({})],
  [
    /^(LaTeX text .*)$/,
    "agentLatexValidationFailed",
    (m) => ({ detail: m[1] }),
  ],

  // 文件冲突（乐观锁）
  [
    /^File (.+) has been modified by another workspace/,
    "fileConflict",
    () => ({}),
  ],

  // PDF
  [/^PDF not found:\s*(.+)/, "pdfNotFound", (m) => ({ path: m[1] })],

  // 服务层通用失败（带 {e} 参数）
  [
    /^Style modification failed:\s*(.+)/,
    "styleModificationFailed",
    (m) => ({ error: m[1] }),
  ],
  [
    /^Template generation failed:\s*(.+)/,
    "templateGenerationFailed",
    (m) => ({ error: m[1] }),
  ],
  [
    /^Formula recognition failed:\s*(.+)/,
    "formulaRecognitionFailed",
    (m) => ({ error: m[1] }),
  ],
  [
    /^Table recognition failed:\s*(.+)/,
    "tableRecognitionFailed",
    (m) => ({ error: m[1] }),
  ],
  [
    /^Format analysis failed:\s*(.+)/,
    "formatAnalysisFailed",
    (m) => ({ error: m[1] }),
  ],
  [
    /^Metadata extraction failed:\s*(.+)/,
    "metadataExtractionFailed",
    (m) => ({ error: m[1] }),
  ],
  [
    /^BibTeX generation failed:\s*(.+)/,
    "bibtexGenerationFailed",
    (m) => ({ error: m[1] }),
  ],
  [
    /^Edit generation failed:\s*(.+)/,
    "editGenerationFailed",
    (m) => ({ error: m[1] }),
  ],
  [
    /^Pandoc conversion failed:\s*(.+)/,
    "pandocConversionFailed",
    (m) => ({ error: m[1] }),
  ],
  [
    /^Image metadata extraction failed:\s*(.+)/,
    "imageMetadataExtractionFailed",
    (m) => ({ error: m[1] }),
  ],

  // 联网搜索
  // DDG
  [
    /^DDG challenge for query:\s*(.+)/,
    "ddgChallenge",
    (m) => ({ query: m[1] }),
  ],
  [/Challenge expired or not found/, "challengeExpired", () => ({})],
  [/Verification failed, please retry/, "verificationFailed", () => ({})],
  [/IP has been blocked, please try again later/, "ipBlocked", () => ({})],

  // 学术研究
  [/^OpenAlex request failed:/, "openAlexRequestFailed", () => ({})],
  [
    /^Scholarly work not found:\s*(.+)/,
    "scholarlyWorkNotFound",
    (m) => ({ id: m[1] }),
  ],
  [/^work_id must be/, "invalidScholarlyWorkId", () => ({})],
  [/^relation_type must be/, "invalidScholarlyRelation", () => ({})],
  [/^resource_type must be/, "invalidResearchResourceType", () => ({})],
  [/^year_from must be/, "invalidScholarlyYearRange", () => ({})],
];

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

  // 2. 模式匹配
  for (const [regex, key, getParams] of PATTERN_MAP) {
    const m = regex.exec(msg);
    if (m) {
      return i18n.t(`errors:${key}`, getParams(m));
    }
  }

  // 3. 无法匹配则原样返回
  return msg;
}
