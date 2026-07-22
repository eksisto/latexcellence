/**
 * 后端错误消息 → 中文翻译模块
 *
 * 设计说明：
 * - 后端 HTTPException 返回的 detail 字段多为英文
 * - 本模块在 API 层统一翻译，确保用户看到的始终是中文
 * - 已经是中文的消息（如 "main_file 不能为空"）无匹配项，原样返回
 */

// ── 静态消息（精确匹配）──────────────────────────────────────────
const EXACT_MAP = {
  // 网络 / 服务不可达
  "Failed to fetch": "获取失败",
  "Request timed out": "请求超时",
  "Internal Server Error": "内部服务器错误",

  // 项目管理
  "No project opened": "未打开任何项目，请先打开一个项目",

  // LLM / API Key
  "No API key configured": "未配置 API 密钥，LLM 功能不可用",
  "Max retries exceeded": "请求次数超限，请稍后重试",

  // 段落应用错误
  "marker_map_missing": "标记映射丢失",

  // 参数校验
  "Empty reference example": "参考文献示例不能为空",
  "Empty metadata": "元数据不能为空",
  "Empty BibTeX code": "BibTeX 代码不能为空",
  "Either text or image must be provided": "请提供文本或图片",
  "Empty image file": "图片文件为空",
  "No file selected": "未选择文件",
  "Empty file": "文件为空",
  "Format name required": "格式名称不能为空",
  "LaTeX code cannot be empty": "LaTeX 代码不能为空",
  "Description cannot be empty": "修改描述不能为空",
  "Markdown text is empty": "Markdown 文本为空",
  "Target paragraph is empty": "目标段落为空",
  "Message not found": "消息不存在",
  "Session not found": "会话不存在",
  "Diff PDF not found": "差异 PDF 不存在",
  "Invalid job id": "无效的任务 ID",
  "Missing content type": "缺少文件类型",
  "Main file must be a .tex file": "主文件必须是 .tex 文件",
  "Git revision must not be empty or padded with spaces": "Git 版本号不能为空或包含空格",
  "Invalid project-relative path": "无效的项目相对路径",
  "Project directory not found in target revision": "目标版本中找不到项目目录",
  "Pandoc produced empty output": "Pandoc 输出为空",

  // Pandoc
  "Pandoc is not installed. Install it from https://pandoc.org/installing.html":
    "未安装 Pandoc，请从 https://pandoc.org/installing.html 安装",
};

// ── 模式消息（正则匹配，$1/$2 引用捕获组）──────────────────────────
const PATTERN_MAP = [
  // 超时
  [/^Compilation timed out after (\d+) seconds/, "编译超时（已等待 $1 秒）"],
  [/^Command timed out after (\d+) seconds/, "命令执行超时（已等待 $1 秒）"],
  [/timed out/, "超时"],
  // 项目管理
  [/^Project path not found:\s*(.+)/, "项目路径不存在：$1"],
  [/^Not a directory:\s*(.+)/, "不是目录：$1"],
  [/^File not found:\s*(.+)/, "文件不存在：$1"],
  [/^Main file not found:\s*(.+)/, "主文件不存在：$1"],

  // 会话 / 提示词 / 格式
  [/^Session not found:\s*(.+)/, "会话不存在：$1"],
  [/^Prompt not found:\s*(.+)/, "提示词不存在：$1"],
  [/^Format not found:\s*(.+)/, "格式不存在：$1"],

  // Git
  [/^Invalid Git revision:\s*(.+)/, "无效的 Git 版本号：$1"],
  [/^File not found in revision (.+?):\s*(.+)/, "在版本 $1 中找不到文件：$2"],

  // 段落索引
  [
    /^Paragraph index (\d+) out of range \(0-(\d+)\)/,
    "段落索引 $1 超出范围 (0-$2)",
  ],

  // 图片
  [
    /^Unsupported image format:\s*(.+?)\.?\s*Supported:/,
    "不支持的图片格式：$1，支持：PNG、JPEG、WebP",
  ],
  [
    /^Image too large:\s*([\d.]+)MB\. Maximum:\s*([\d.]+)MB/,
    "图片过大：$1MB，最大：$2MB",
  ],

  // 文件修改冲突
  [
    /^File (.+) has been modified since parsing/,
    "文件 $1 自解析后已被修改，请重新加载",
  ],

  // PDF
  [/^PDF not found:\s*(.+)/, "PDF 不存在：$1，请先编译项目"],

  // 服务层通用失败（带 {e} 参数）
  [/^Style modification failed:\s*(.+)/, "样式修改失败：$1"],
  [/^Template generation failed:\s*(.+)/, "模板生成失败：$1"],
  [/^Formula recognition failed:\s*(.+)/, "公式识别失败：$1"],
  [/^Table recognition failed:\s*(.+)/, "表格识别失败：$1"],
  [/^Format analysis failed:\s*(.+)/, "格式分析失败：$1"],
  [/^Metadata extraction failed:\s*(.+)/, "元数据提取失败：$1"],
  [/^BibTeX generation failed:\s*(.+)/, "BibTeX 生成失败：$1"],
  [/^Edit generation failed:\s*(.+)/, "编辑生成失败：$1"],
  [/^Pandoc conversion failed:\s*(.+)/, "Pandoc 转换失败：$1"],
  [/^Image metadata extraction failed:\s*(.+)/, "图片元数据提取失败：$1"],

  // 联网搜索
  // DDG
  [/^DDG challenge for query:\s*(.+)/, "搜索引擎需要验证：$1"],
  ["Challenge expired or not found", "验证已过期或不存在，请重新提问"],
  ["Verification failed, please retry", "验证失败，请重试"],
  ["IP has been blocked, please try again later", "IP 已被封禁，请稍后重试"],
];

/**
 * 将后端英文错误消息翻译为中文
 * @param {string} msg 原始错误消息
 * @returns {string} 中文错误消息（无法匹配时原样返回）
 */
export function translateError(msg) {
  if (!msg) return "未知错误";

  // 1. 精确匹配
  if (msg in EXACT_MAP) return EXACT_MAP[msg];

  // 2. 模式匹配
  for (const [regex, replacement] of PATTERN_MAP) {
    if (regex.exec(msg)) {
      return msg.replace(regex, replacement);
    }
  }

  // 3. 无法匹配则原样返回
  return msg;
}
