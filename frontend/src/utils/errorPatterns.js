/**
 * 后端错误消息模式匹配核心（纯模块）
 *
 * PATTERN_MAP 从 errorMessages.js 抽出，便于在 node --test 下无副作用地
 * 导入单测：errorMessages.js 顶层 import @/i18n 会拉起 i18next 运行时与
 * 浏览器 LanguageDetector，无法在 Node 环境干净加载；EXACT_MAP 则必须
 * 留在 errorMessages.js——frontend/test/multiUserLocales.test.mjs 以源码
 * 文本解析它，作为三语错误码覆盖校验的单一事实源。
 *
 * 本模块只包含纯数据（PATTERN_MAP）与纯函数（matchPattern），不依赖
 * i18n 运行时、DOM 或浏览器 API；精确匹配（EXACT_MAP）与 i18n 插值由
 * errorMessages.js 负责。
 */

// ── 模式消息（正则匹配，提取参数后由调用方用 i18n.t() 翻译）──────
export const PATTERN_MAP = [
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
  // 占位符与后端 detail.retry_after 字段同名，结构化路径直接透传即可插值。
  // 不加 ^ 锚定：逐段检查等 SSE 流式路径会拼接 "{文件名} P{段号}: " 前缀
  // （backend/core/base_paragraph_check.py），消息形如
  // "main.tex P3: LLM rate limit exceeded (21/20 per 60s); retry after 42s"。
  // 模式含完整字面短语 + 数字配额格式，足够特异，不会误伤登录/Web 限流等
  // 其他 rate limit 条目（那些走 EXACT_MAP 精确匹配，且本就先于模式匹配）。
  [
    /LLM rate limit exceeded \(\d+\/\d+ per \d+s\); retry after (\d+)s/,
    "llmRateLimited",
    (m) => ({ retry_after: m[1] }),
  ],
  [
    /LLM concurrency limit reached \(\d+\/\d+\); retry after (\d+)s/,
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
  // plot_gen Agent 止损指令（f-string 带工具名/失败次数）
  [
    /^STOP: the tool call `(.+?)` has failed with the identical result multiple times in a row\./,
    "plotGenRepeatFailStop",
    (m) => ({ tool: m[1] }),
  ],
  [
    /^STOP: `python_sandbox` has failed (\d+) times in a row\./,
    "plotGenSandboxBudgetStop",
    (m) => ({ limit: m[1] }),
  ],
  [
    /^STOP: `compile_tikz` has failed (\d+) times in a row\./,
    "plotGenTikzBudgetStop",
    (m) => ({ limit: m[1] }),
  ],

  // template_gen Agent 收尾（带轮数）
  [
    /^Reached the maximum number of rounds \((\d+)\), but the last compilation still did not pass\./,
    "templateGenMaxRoundsCompileFailed",
    (m) => ({ rounds: m[1] }),
  ],

  // 单用户模式路径浏览（project_router）
  [/^Path does not exist:\s*(.+)/, "pathNotExist", (m) => ({ path: m[1] })],
  [/^Permission denied:\s*(.+)/, "permissionDenied", (m) => ({ path: m[1] })],

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
 * 对原始消息执行模式匹配，返回首个命中条目的 i18n 键与插值参数。
 *
 * 注意：本函数只做模式匹配；调用方（translateError）须先查 EXACT_MAP
 * 精确匹配，未命中再调用本函数，以保持「先精确、后模式」的历史语义。
 *
 * @param {string} msg 原始错误消息
 * @returns {{ key: string, params: object } | null} 首个命中条目；无法匹配时返回 null
 */
export function matchPattern(msg) {
  for (const [regex, key, getParams] of PATTERN_MAP) {
    const m = regex.exec(msg);
    if (m) {
      return { key, params: getParams(m) };
    }
  }
  return null;
}
