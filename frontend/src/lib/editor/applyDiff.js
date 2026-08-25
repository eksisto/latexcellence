/**
 * Unified diff 应用纯模块（editor-plan §7.2 建议形态三，阶段四）。
 *
 * 职责：解析助手输出的 ```diff 围栏内容（unified diff），逐 hunk 以
 * 上下文行锚定到当前 CM6 缓冲区文本并应用，返回新缓冲区全文。
 *
 * 设计约束：
 *   - 纯函数、零依赖，供 node --test 直接单测；
 *   - 上下文锚定匹配：优先按 hunk 头提示的旧行号定位，失配时向两侧
 *     逐步偏移搜索完整旧块精确匹配（容忍 LLM 给出的行号漂移）；
 *   - 任一 hunk 无法定位即整体失败（不落半套改动），由调用方 toast 反馈；
 *   - 行级操作：输入/输出均为以 \n 连接的整段文本；\ No newline 标记
 *     仅忽略不参与匹配。
 */

/** @typedef {{type:"context"|"add"|"del", text:string}} DiffLine */

const HUNK_HEADER_RE = /^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@/;
const MAX_SEARCH_OFFSET = 200;

/**
 * 解析 unified diff 文本。
 * @param {string} diffText
 * @returns {{ok: true, hunks: Array<{oldStart:number, newStart:number, lines:DiffLine[]}, additions:number, deletions:number>}
 *           | {ok:false}}
 */
export function parseUnifiedDiff(diffText) {
  if (typeof diffText !== "string" || diffText.trim() === "") {
    return { ok: false };
  }
  // 去掉结尾换行，避免文本末行之后的空串被误判为空 context 行
  const lines = diffText.replace(/\n+$/, "").split("\n");
  // 容忍 git 头部行（---/+++）
  const hunks = [];
  let current = null;
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    const header = HUNK_HEADER_RE.exec(line);
    if (header) {
      current = {
        oldStart: parseInt(header[1], 10),
        newStart: parseInt(header[3], 10),
        lines: [],
      };
      hunks.push(current);
      continue;
    }
    if (!current) continue; // hunk 头之前的文件头等行忽略
    if (line.startsWith("\\")) continue; // "\ No newline at end of file"
    if (line.startsWith(" ")) {
      current.lines.push({ type: "context", text: line.slice(1) });
    } else if (line.startsWith("+")) {
      current.lines.push({ type: "add", text: line.slice(1) });
    } else if (line.startsWith("-")) {
      current.lines.push({ type: "del", text: line.slice(1) });
    } else if (line === "") {
      // LLM 输出常在 hunk 之间插入空行分隔：紧邻下一 hunk 头的连续空行是
      // 分隔噪声而非内容，跳过；否则会被误当作空 context 行追加到前一个
      // hunk 的旧块，导致锚定失配、整份 diff 被误判为整体失败
      let j = i;
      while (j + 1 < lines.length && lines[j + 1] === "") j++;
      if (j + 1 < lines.length && HUNK_HEADER_RE.exec(lines[j + 1])) {
        continue;
      }
      // 其余位置的空行视为被剥掉前导空格的空 context 行
      current.lines.push({ type: "context", text: "" });
    }
    // 其他未知前缀行：忽略，保持解析宽松
  }
  if (hunks.length === 0 || hunks.some((h) => h.lines.length === 0)) {
    return { ok: false };
  }
  let additions = 0;
  let deletions = 0;
  for (const h of hunks) {
    for (const l of h.lines) {
      if (l.type === "add") additions += 1;
      else if (l.type === "del") deletions += 1;
    }
  }
  return { ok: true, hunks, additions, deletions };
}

/**
 * 计算 hunk 的期望旧行块与替换行块。
 * @returns {{oldLines: string[], newLines: string[]}}
 */
function hunkParts(hunk) {
  const oldLines = [];
  const newLines = [];
  for (const l of hunk.lines) {
    if (l.type === "context") {
      oldLines.push(l.text);
      newLines.push(l.text);
    } else if (l.type === "del") {
      oldLines.push(l.text);
    } else {
      newLines.push(l.text);
    }
  }
  return { oldLines, newLines };
}

function matchesAt(bufferLines, index, oldLines) {
  if (index < 0 || index + oldLines.length > bufferLines.length) return false;
  for (let i = 0; i < oldLines.length; i++) {
    if (bufferLines[index + i] !== oldLines[i]) return false;
  }
  return true;
}

/**
 * 在缓冲区中为单个 hunk 定位应用点（0-based 插入下标）。
 *
 * 有旧块（context/del）时：优先使用 hunk 头提示的 oldStart，失配时向
 * 两侧扩展搜索完整旧块精确匹配；纯插入 hunk（无旧块）按 git 语义
 * "插入到旧第 oldStart 行之后"，仅信任行号。
 * @returns {number} 应用点下标；-1 表示未找到
 */
function locateHunk(bufferLines, hunk) {
  const { oldLines } = hunkParts(hunk);
  if (oldLines.length === 0) {
    return Math.min(Math.max(hunk.oldStart, 0), bufferLines.length);
  }
  const hint = Math.max(0, hunk.oldStart - 1);
  for (let offset = 0; offset <= MAX_SEARCH_OFFSET; offset++) {
    for (const candidate of offset === 0
      ? [hint]
      : [hint - offset, hint + offset]) {
      if (matchesAt(bufferLines, candidate, oldLines)) return candidate;
    }
  }
  return -1;
}

/**
 * 将 unified diff 应用到缓冲区文本（§7.2 建议形态三）。
 *
 * @param {string} bufferText 当前 CM6 缓冲区全文
 * @param {string} diffText 助手输出的 unified diff 文本
 * @returns {{ok: true, result: string}
 *           | {ok: false, reason: "empty_diff"|"malformed_diff"|"hunk_not_matched", detail?: string}}
 */
export function applyUnifiedDiff(bufferText, diffText) {
  const parsed = parseUnifiedDiff(diffText);
  if (!parsed.ok) {
    return {
      ok: false,
      reason:
        typeof diffText === "string" && diffText.trim() !== ""
          ? "malformed_diff"
          : "empty_diff",
    };
  }
  const bufferLines = String(bufferText ?? "").split("\n");
  // 按缓冲区位置从后往前应用，避免前序 hunk 的长度变化影响后续定位
  const located = parsed.hunks.map((hunk) => ({
    hunk,
    at: locateHunk(bufferLines, hunk),
  }));
  const failed = located.find((l) => l.at === -1);
  if (failed) {
    return { ok: false, reason: "hunk_not_matched" };
  }
  const ordered = [...located].sort((a, b) => b.at - a.at);
  for (const { hunk, at } of ordered) {
    const { oldLines, newLines } = hunkParts(hunk);
    bufferLines.splice(at, oldLines.length, ...newLines);
  }
  return { ok: true, result: bufferLines.join("\n") };
}
