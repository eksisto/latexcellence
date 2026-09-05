/**
 * \begin{env} 自动配对（editor-productivity-design §5.3 阶段二 / 决策 Q12）。
 *
 * Overleaf 式打字机补 \end：输入 `\begin{env}` 的右花括号 `}` 后自动插入
 * `\n\n\end{env}`，光标留在中间空行；若该 `\begin` 之后的缓冲区内已存在
 * 对应 `\end{env}`（向后扫描，跳过嵌套同名环境），则不再补（曾配对不重复）；
 * `%` 注释行内的 `\begin` 不触发。
 *
 * 分层：
 *   1. 纯函数层（node --test 直接覆盖）：beginAutoCloseDecision 只看
 *      「光标前文本 / 光标后文本」做决策，输出插入文本与光标偏移；
 *   2. CM6 层：transactionFilter 监听打字事务——新文档中光标恰落在某个
 *      插入区间的末尾（刚敲入 `}`）时追加补全事务；多光标下每个落在
 *      插入末尾的光标各自决策，同一事务完成。
 *
 * 开关：autoCloseEnv 偏好（默认开）经 isEnabled 注入（每次事务时求值，
 * 偏好切换无须重配置扩展）；readOnly 状态短路。
 */

import { EditorSelection, EditorState } from "@codemirror/state";

/** 光标前文本结尾的 `\begin{env}`（env 允许字母 / 数字 / 星号） */
const BEGIN_ENV_TAIL_RE = /\\begin\{([A-Za-z0-9*]+)\}$/;

/** 光标前文本参与尾匹配的窗口长度（`\begin{...}` 最长几十字符，取整量级） */
const TAIL_WINDOW = 120;

/** 行内环境标记（`\begin{env}` / `\end{env}`；仅用于向后扫描已配对判断） */
const ENV_TOKEN_RE = /\\(begin|end)\{([A-Za-z0-9*]+)\}/g;

/**
 * 行内首个未转义 `%`（LaTeX 注释起点）的下标；无注释返回 -1。
 * `\%` 为字面百分号（前置奇数个反斜杠视为转义）。
 */
function findCommentIndex(line) {
  for (let i = 0; i < line.length; i += 1) {
    if (line[i] !== "%") continue;
    let backslashes = 0;
    for (let j = i - 1; j >= 0 && line[j] === "\\"; j -= 1) backslashes += 1;
    if (backslashes % 2 === 0) return i;
  }
  return -1;
}

/**
 * 向后扫描缓冲区剩余部分，判断该 `\begin{env}` 是否已有对应 `\end{env}`：
 *  - 嵌套同名环境：先遇到的 `\begin{env}` 抵消一层深度（其后的同名 `\end`
 *    配对的是嵌套层，不是本 `\begin`）；
 *  - 注释行（行内首个未转义 `%` 之后）不参与配对判断。
 * 扫描到「深度为 0 时遇到的 `\end{env}`」即认定本 `\begin` 已配对。
 */
function hasMatchingEndAfter(afterText, env) {
  const tokenRe = new RegExp(ENV_TOKEN_RE.source, "g"); // 独立 lastIndex
  let depth = 0;
  for (const rawLine of afterText.split("\n")) {
    const commentAt = findCommentIndex(rawLine);
    const line = commentAt === -1 ? rawLine : rawLine.slice(0, commentAt);
    let m;
    tokenRe.lastIndex = 0;
    while ((m = tokenRe.exec(line)) !== null) {
      if (m[2] !== env) continue;
      if (m[1] === "begin") {
        depth += 1;
      } else if (depth === 0) {
        return true;
      } else {
        depth -= 1;
      }
    }
  }
  return false;
}

/**
 * 配对决策纯函数。
 *
 * @param {string} beforeText 新文档中光标之前的文本（可传入尾部窗口切片，
 *   正则锚定结尾，不受切片起点影响）
 * @param {string} afterText 新文档中光标之后的文本（用于向后扫描 \end）
 * @returns {{env: string, insert: string, cursorOffset: number}|null}
 *   需要补全时返回：env 环境名、insert 在光标处插入的文本
 *   （`\n\n` + `\begin` 行首缩进 + `\end{env}`）、cursorOffset 光标相对
 *   插入点的偏移（1 = 第一个 \n 之后、中间空行行首）；不满足条件返回 null。
 */
export function beginAutoCloseDecision(beforeText, afterText) {
  if (typeof beforeText !== "string" || typeof afterText !== "string") {
    return null;
  }
  const m = BEGIN_ENV_TAIL_RE.exec(beforeText);
  if (!m) return null;
  const env = m[1];
  // 该 `\begin` 位于同行 `%` 注释内（\begin 前有未转义 %）→ 不触发
  const lineStart = beforeText.lastIndexOf("\n") + 1;
  if (findCommentIndex(beforeText.slice(lineStart, m.index)) !== -1) {
    return null;
  }
  // 该 \begin 之后已有对应 \end{env}（向后扫描、跳过嵌套同名环境）→ 不再补
  if (hasMatchingEndAfter(afterText, env)) return null;
  // 缩进：\begin 所在行的行首空白
  const indent = /^\s*/.exec(beforeText.slice(lineStart))[0];
  return {
    env,
    insert: `\n\n${indent}\\end{${env}}`,
    cursorOffset: 1,
  };
}

/**
 * \begin 自动配对扩展。
 * @param {{isEnabled?: () => boolean}} [options]
 *   isEnabled：autoCloseEnv 偏好读取器（每事务求值；缺省恒开）。
 * @returns {import("@codemirror/state").Extension}
 */
export function autoCloseEnvExtension({ isEnabled = () => true } = {}) {
  return EditorState.transactionFilter.of((tr) => {
    if (!tr.docChanged) return tr;
    if (tr.state.readOnly) return tr;
    if (!isEnabled()) return tr;
    // 仅响应打字 / 粘贴类输入（自身追加的补全事务光标不在插入末尾，
    // 天然不复发；程序性整缓冲区替换无 input userEvent，被此处挡下）
    if (!tr.isUserEvent("input")) return tr;

    // 新文档坐标系中发生插入的区间（to === 光标 = 该光标刚完成输入）
    const insertedTo = [];
    tr.changes.iterChangedRanges((fromA, toA, fromB, toB) => {
      if (toB > fromB) insertedTo.push(toB);
    });
    if (insertedTo.length === 0) return tr;
    const doc = tr.newDoc;
    const selection = tr.newSelection;
    if (!selection) return tr;

    // 逐光标决策（多光标：每个落在插入末尾的光标独立判断）
    const pending = []; // {pos, insert, cursorOffset}（升序）
    for (const range of selection.ranges) {
      if (range.from !== range.to) continue;
      const pos = range.anchor;
      if (!insertedTo.some((end) => end === pos)) continue;
      const decision = beginAutoCloseDecision(
        doc.sliceString(Math.max(0, pos - TAIL_WINDOW), pos),
        doc.sliceString(pos),
      );
      if (decision) {
        pending.push({
          pos,
          insert: decision.insert,
          cursorOffset: decision.cursorOffset,
        });
      }
    }
    if (pending.length === 0) return tr;

    // 追加事务：插入 \end 段并把每个触发光标移入中间空行；其余光标按
    // 插入偏移映射。新选区坐标基于「原事务 + 本事务变更之后」的文档。
    const selRanges = [];
    let shift = 0;
    let pi = 0;
    for (const range of selection.ranges) {
      while (pi < pending.length && pending[pi].pos <= range.anchor) {
        shift += pending[pi].insert.length;
        pi += 1;
      }
      const own = pending.find((p) => p.pos === range.anchor);
      if (own && range.from === range.to) {
        // 触发光标：落在中间空行（两个换行符之间）
        const cursor =
          range.anchor + shift - own.insert.length + own.cursorOffset;
        selRanges.push(EditorSelection.cursor(cursor));
      } else {
        selRanges.push(
          EditorSelection.range(range.anchor + shift, range.head + shift),
        );
      }
    }
    return [
      tr,
      {
        // 追加事务在原事务之后的新文档上应用（必须显式 sequential，
        // 否则 CM6 会把两个 spec 当作同层并行合并，坐标错位）
        sequential: true,
        changes: pending.map((p) => ({ from: p.pos, insert: p.insert })),
        selection: EditorSelection.create(selRanges),
        scrollIntoView: true,
        userEvent: "input.complete",
      },
    ];
  });
}
