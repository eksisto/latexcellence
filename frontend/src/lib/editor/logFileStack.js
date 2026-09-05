/**
 * logFileStack —— 编译日志文件栈解析纯模块（editor-productivity-design
 * 阶段七 / 决策 Q19）。
 *
 * 解析 TeX 编译日志（compile API 失败响应携带的 stdout 原始日志，
 * 后端已把工作目录前缀脱敏为 `./`），把每条 `l.N` 错误归属到所在文件：
 *   - `(./path` 开括号压栈、`)` 括号弹栈（TeX 日志的文件嵌套约定）；
 *   - `l.N` 错误行归属当前栈顶文件，支持跨文件跳转；
 *   - 歧义保守处理：`\(` / `\)` 数学括号、消息文本中的成对括号
 *     （如 `(see the transcript)`、`Overfull \hbox (15.3pt too wide)`）
 *     不参与文件栈运算；错误块（`!` 行到 `l.N` 行）上下文收录期间跳过
 *     栈运算，上下文收满仍无 `l.N`（无行号错误）则恢复扫描，防止错误
 *     块无限吞行导致栈失配；空栈弹栈等畸形日志置 stackBroken，此后错误
 *     回落当前缓冲区（发起编译的文件）并标 `lowConfidence: true`；
 *     栈顶为系统文件（texmf 树绝对路径）或项目外路径时同样回落；
 *   - TeX 日志按 max_print_line（79 列）折行：先按 79 列启发式 dewrap
 *     再逐逻辑行解析，长文件名 / 长错误消息的跨行折行因此正确续接。
 *
 * 纯函数、零副作用，可 node --test 单测；正则均为线性扫描，
 * 无嵌套量词（AGENTS.md 正则规范）。
 */

import { isEditableFilePath } from "./files.js";

/** TeX 默认 max_print_line：折行段必打满该列数（79 列启发式依据） */
const WRAP_COLS = 79;

/** 无 `/` 前缀裸文件名的扩展名白名单（`(main.tex` / `(article.cls` 形态） */
const BARE_FILE_EXT_RE =
  /\.(?:tex|sty|cls|dtx|ltx|clo|def|fd|cfg|ldf|aux|toc|lof|lot|out|bbl|bib|bst|log|mk)$/i;

/** `l.N` 错误行号：逻辑行内任意位置（折行 dewrap 后通常行首），词边界防误命中 */
const LINE_NO_RE = /(?:^|\s)l\.(\d+)(?=\s|$)/;

/** 错误条目数上限：防御性日志（如无限循环输出）不拖垮右栏渲染 */
const MAX_ERRORS = 200;

/** 错误块（`!` 行到 `l.N` 行之间）上下文行收录上限 */
const MAX_CONTEXT_LINES = 3;

/**
 * 日志 dewrap：折行段必然打满 max_print_line，不足 79 列的物理行即
 * 逻辑行结尾。恰好 79 列的完整逻辑行会被误并入下一行——保守启发式，
 * 只影响消息/detail 文本拼接，不影响括号栈正确性。
 */
function dewrapLog(logText) {
  const physical = String(logText ?? "")
    .replace(/\r\n?/g, "\n")
    .split("\n");
  const lines = [];
  let pending = "";
  for (const line of physical) {
    pending += line;
    if (line.length >= WRAP_COLS) continue;
    lines.push(pending);
    pending = "";
  }
  if (pending) lines.push(pending);
  return lines;
}

/**
 * 括号后 token 是否形似文件路径：
 *   - `./` / `../` 前缀、texmf 树绝对路径 → 是（绝对路径压栈仅为配平，
 *     归属时经 normalizeProjectRelativePath 回落）；
 *   - 含 `/` 且末段含 `.`（`texmf-dist/.../longtable.sty`）→ 是；
 *   - 裸名命中扩展名白名单（`main.tex`）→ 是；
 *   - 其余（`see`、`15.3pt` 等消息文本）→ 否，按文本括号记账。
 */
function looksLikeFilePath(token) {
  if (!token) return false;
  if (
    token.includes("\\") ||
    token.includes("`") ||
    token.includes("'") ||
    token.includes("*") ||
    token.includes("$") ||
    token.includes("%")
  ) {
    return false;
  }
  if (
    token.startsWith("./") ||
    token.startsWith("../") ||
    token.startsWith("/")
  ) {
    return true;
  }
  const slashIndex = token.indexOf("/");
  if (slashIndex !== -1) {
    return token.slice(token.lastIndexOf("/") + 1).includes(".");
  }
  return BARE_FILE_EXT_RE.test(token);
}

/**
 * 路径归一化：剥离 `./` 前缀后返回项目内相对路径；系统文件（绝对路径）
 * 与项目外路径（`../`）返回 null（不可跳转，归属回落当前缓冲区）。
 */
export function normalizeProjectRelativePath(rawPath) {
  if (typeof rawPath !== "string") return null;
  let p = rawPath.trim();
  while (p.startsWith("./")) p = p.slice(2);
  if (!p || p.startsWith("/") || p.startsWith("../") || p.includes("/../")) {
    return null;
  }
  return p;
}

/**
 * 单逻辑行括号扫描：维护文件栈与「文本括号深度」。
 * `(` + 形似路径的 token → 压栈；否则文本括号深度 +1（其配对 `)` 只回退
 * 深度、不弹文件栈，隔离 `(see ...)` / `(15.3pt ...)` 类消息括号）。
 * `\(` / `\)` 数学括号完全不参与；空栈弹栈置 stackBroken（畸形日志）。
 */
function scanParenLine(line, stack, state) {
  const len = line.length;
  for (let i = 0; i < len; i += 1) {
    const ch = line[i];
    if (ch === "(") {
      if (i > 0 && line[i - 1] === "\\") continue; // \(
      let j = i + 1;
      while (
        j < len &&
        line[j] !== " " &&
        line[j] !== "\t" &&
        line[j] !== "(" &&
        line[j] !== ")"
      ) {
        j += 1;
      }
      if (looksLikeFilePath(line.slice(i + 1, j))) {
        stack.push(line.slice(i + 1, j));
      } else {
        state.textDepth += 1;
      }
      if (line[j] === ")")
        i = j - 1; // 闭括号交给下一轮统一处理
      else i = j; // 跳过 token 本体
    } else if (ch === ")") {
      if (i > 0 && line[i - 1] === "\\") continue; // \)
      if (state.textDepth > 0) state.textDepth -= 1;
      else if (stack.length > 0) stack.pop();
      else state.stackBroken = true;
    }
  }
}

/** 错误归属：栈顶项目内白名单文件（.tex/.bib/.sty/.cls）→ 高置信；
 ** 其余（系统文件、.aux 等不可编辑路径、歧义、畸形）一律回落低置信 */
function resolveErrorTarget(stack, state, fallbackFile) {
  if (!state.stackBroken && stack.length > 0) {
    const rel = normalizeProjectRelativePath(stack[stack.length - 1]);
    if (rel && isEditableFilePath(rel)) {
      return { file: rel, lowConfidence: false };
    }
  }
  return { file: fallbackFile, lowConfidence: true };
}

function trimMessage(line) {
  return line
    .replace(/^!+\s*/, "")
    .trim()
    .slice(0, 200);
}

/**
 * 解析编译日志，输出可点击错误条目数组。
 *
 * @param {string} logText 编译日志原文（compile API 失败响应的 stdout）
 * @param {string|null} fallbackFile 发起编译的文件（项目相对路径），
 *   归属歧义 / 畸形日志 / 无文件栈时回落目标
 * @returns {Array<{file: string|null, line: number, message: string,
 *   detail: string, lowConfidence: boolean}>}
 */
export function parseLogErrors(logText, fallbackFile = null) {
  const fallback = normalizeProjectRelativePath(fallbackFile);
  if (!logText) return [];
  const stack = [];
  const state = { textDepth: 0, stackBroken: false };
  const errors = [];
  /** 错误块：`!` 消息行开启，`l.N` 行关闭并产出条目 */
  let block = null;

  for (const line of dewrapLog(logText)) {
    if (block) {
      const match = LINE_NO_RE.exec(line);
      if (match) {
        const { file, lowConfidence } = resolveErrorTarget(
          stack,
          state,
          fallback,
        );
        const snippet = line
          .slice(match.index + match[0].length)
          .trim()
          .slice(0, 160);
        errors.push({
          file,
          line: Number.parseInt(match[1], 10),
          message: block.message,
          detail: snippet || block.context.join(" ").slice(0, 200),
          lowConfidence,
        });
        block = null;
        continue; // l.N 行的源码回显可能含裸括号，不参与栈运算
      }
      if (line.startsWith("!")) {
        block = { message: trimMessage(line), context: [] }; // 新错误接管
        continue;
      }
      if (line.length > 0) {
        if (block.context.length < MAX_CONTEXT_LINES) {
          block.context.push(line.trim().slice(0, 200));
          continue;
        }
        // 上下文收满仍无 l.N（如 `! File not found` 类无行号错误）：
        // 恢复本行的文件栈扫描——错误块不得无限吞行，否则后续 `(./`
        // 开括号与 `)` 闭括号失配，后续错误会被误归属到陈旧栈顶（且
        // 标高置信）；block 保持开启，更晚出现的 l.N 行仍可关闭记录
        scanParenLine(line, stack, state);
      }
      continue;
    }
    if (line.startsWith("!") && line.length > 1) {
      block = { message: trimMessage(line), context: [] };
      continue;
    }
    scanParenLine(line, stack, state);
  }
  return errors.slice(0, MAX_ERRORS);
}
