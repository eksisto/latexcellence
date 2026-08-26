/**
 * Unified diff 应用纯模块（editor-plan §7.2 建议形态三，阶段四）。
 *
 * 职责：解析助手输出的 ```diff 围栏内容（unified diff），逐 hunk 以
 * 上下文行锚定到当前 CM6 缓冲区文本并应用，返回新缓冲区全文。
 *
 * 设计约束：
 *   - 纯函数、零依赖，供 node --test 直接单测；
 *   - 上下文锚定匹配：优先按 hunk 头提示的旧行号定位，失配时向两侧
 *     逐步偏移搜索完整旧块精确匹配（容忍 LLM 给出的行号漂移）；窗口内
 *     命中多处即判定锚定歧义；
 *   - 任一 hunk 无法定位、命中歧义或源区间重叠即整体失败（不落半套
 *     改动），由调用方按 reason toast 反馈；
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
 * 两侧扩展搜索完整旧块精确匹配；收集 ±MAX_SEARCH_OFFSET 窗口内全部
 * 精确命中，命中数 ≥2 视为锚定歧义（同一旧块在窗口内多处出现时应用
 * 位置不确定，宁整体失败也不误改）。纯插入 hunk（无旧块）无内容锚点，
 * 按 git 语义"插入到旧第 oldStart 行之后"，仅信任行号。
 * @returns {{status:"matched"|"not_matched"|"ambiguous", at:number}}
 *           at 仅在 status==="matched" 时有效（歧义/失配为 -1）
 */
function locateHunk(bufferLines, hunk) {
  const { oldLines } = hunkParts(hunk);
  if (oldLines.length === 0) {
    return {
      status: "matched",
      at: Math.min(Math.max(hunk.oldStart, 0), bufferLines.length),
    };
  }
  const hint = Math.max(0, hunk.oldStart - 1);
  const hits = [];
  for (let offset = 0; offset <= MAX_SEARCH_OFFSET; offset++) {
    for (const candidate of offset === 0
      ? [hint]
      : [hint - offset, hint + offset]) {
      if (matchesAt(bufferLines, candidate, oldLines)) hits.push(candidate);
    }
  }
  if (hits.length === 1) return { status: "matched", at: hits[0] };
  return { status: hits.length > 1 ? "ambiguous" : "not_matched", at: -1 };
}

/**
 * 将 unified diff 应用到缓冲区文本（§7.2 建议形态三）。
 *
 * @param {string} bufferText 当前 CM6 缓冲区全文
 * @param {string} diffText 助手输出的 unified diff 文本
 * @returns {{ok: true, result: string}
 *           | {ok: false, reason: "empty_diff"|"malformed_diff"
 *               |"hunk_not_matched"|"hunk_ambiguous"|"hunk_overlap",
 *              detail?: string}}
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
  const located = parsed.hunks.map((hunk) => ({
    hunk,
    parts: hunkParts(hunk),
    ...locateHunk(bufferLines, hunk),
  }));
  // 任一 hunk 定位失配或命中歧义即整体失败（与既有整体失败语义一致，
  // 不落半套改动）
  const failed = located.find((l) => l.status !== "matched");
  if (failed) {
    return {
      ok: false,
      reason:
        failed.status === "ambiguous" ? "hunk_ambiguous" : "hunk_not_matched",
    };
  }
  // 重叠校验：各 hunk 源区间（纯插入长度记 0）按位置升序相邻比较，
  // 后一区间起点落入前一区间 [at, at + oldLen) 即源区间相交 → 整体失败。
  // 同位置的纯插入对（0 长度区间）不相交，照常放行；区间终点之后的
  // 插入点不受影响。倒序 splice 主循环保持不变。
  const ascended = [...located].sort((a, b) => a.at - b.at);
  for (let i = 1; i < ascended.length; i += 1) {
    const cur = ascended[i - 1];
    const next = ascended[i];
    if (next.at < cur.at + cur.parts.oldLines.length) {
      return { ok: false, reason: "hunk_overlap" };
    }
  }
  // 按缓冲区位置从后往前应用，避免前序 hunk 的长度变化影响后续定位
  const ordered = [...located].sort((a, b) => b.at - a.at);
  for (const { parts, at } of ordered) {
    bufferLines.splice(at, parts.oldLines.length, ...parts.newLines);
  }
  return { ok: true, result: bufferLines.join("\n") };
}
