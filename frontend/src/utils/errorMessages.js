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
  // 网络 / 服务不可达
  "Failed to fetch": "fetchFailed",
  "Request timed out": "requestTimedOut",
  "Internal Server Error": "internalServerError",

  // 项目管理
  "No project opened": "noProjectOpened",

  // LLM / API Key
  "No API key configured": "noApiKeyConfigured",
  "Max retries exceeded": "maxRetriesExceeded",

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
  "Pandoc produced empty output": "pandocEmptyOutput",

  // Pandoc
  "Pandoc is not installed. Install it from https://pandoc.org/installing.html":
    "pandocNotInstalled",
};

// ── 模式消息（正则匹配，提取参数后用 i18n.t() 翻译）────────────────
const PATTERN_MAP = [
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
  [
    /^Invalid Git revision:\s*(.+)/,
    "invalidGitRevision",
    (m) => ({ revision: m[1] }),
  ],
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
 * 将后端英文错误消息翻译为当前语言
 * @param {string} msg 原始错误消息
 * @returns {string} 翻译后的错误消息（无法匹配时原样返回）
 */
export function translateError(msg) {
  if (!msg) return i18n.t("errors:unknown");

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
