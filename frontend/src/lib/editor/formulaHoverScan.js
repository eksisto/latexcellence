/**
 * KaTeX 悬停预览的公式区域识别纯模块（editor-productivity-plan §5.3 阶段六 /
 * 决策 Q9；识别口径与 utils/formulaWrap.js 同源思路）。
 *
 * 支持的公式形态：
 *   - 行内 `$...$`（`\$` 为字面美元，不算定界符；`$$` 是 display，不当作两个行内）
 *   - 行内 `\(...\)`
 *   - display `$$...$$` 与 `\[...\]`
 *   - equation / align 系显示环境（`\begin{env}` … `\end{env}`，环境清单与
 *     formulaWrap.js 的 STANDALONE_DISPLAY_ENV_RE 口径一致；同名环境嵌套按
 *     深度配对）
 *   - 注释行（行内首个未转义 `%` 之后）内的内容不参与定界符匹配，也不判定
 *     为公式（`\%` 为字面百分号）
 *   - verbatim 类环境（verbatim / lstlisting / minted 等）不做特殊处理，
 *     其内部的 `$...$` 仍按定界符判定——与 utils/formulaWrap.js 同源口径
 *     保持一致（formulaWrap 同样不感知 verbatim）
 *   - 未闭合的定界符 / 环境不判定为公式
 *   - 顶层转义对（`\\`、`\$`、`\%` 等）覆盖的字符是字面字符：悬停其上
 *     不判定为公式（`\$` 的 `$`、`\\(` 的 `(` 均不开启新区域）
 *
 * 性能策略（hover 回调必须轻）：
 *   单向单遍状态机扫描。CM6 hoverTooltip 挂 500ms 悬停延迟（hoverTime），
 *   回调只在指针停留后触发一次，不存在 mousemove 高频调用；单次调用扫描
 *   范围 = 文档起点 → 悬停位置（O(pos)，命中公式时再向后补扫到闭合定界符），
 *   不做全文重扫、不建索引。数学区域可以任意长，无持久化索引时「起点 → pos」
 *   的前缀扫描已是最小正确判定；典型 .tex（百 KB 级）单次扫描为亚毫秒量级。
 *
 * 纯函数，供 node --test 直接单测（test/editorFormulaHoverScan.test.mjs）。
 */

/**
 * 自带显示模式的顶层环境清单（与 utils/formulaWrap.js 的
 * STANDALONE_DISPLAY_ENV_RE 环境口径一致，含带星变体）。
 */
export const DISPLAY_MATH_ENVS = Object.freeze([
  "equation",
  "equation*",
  "displaymath",
  "align",
  "align*",
  "alignat",
  "alignat*",
  "flalign",
  "flalign*",
  "gather",
  "gather*",
  "multline",
  "multline*",
  "eqnarray",
  "eqnarray*",
]);

const DISPLAY_ENV_SET = new Set(DISPLAY_MATH_ENVS);

/** 是否为自带显示模式的顶层环境名。 */
export function isDisplayMathEnv(name) {
  return DISPLAY_ENV_SET.has(name);
}

/**
 * 悬停位置命中的公式区域。
 * @typedef {object} FormulaRegion
 * @property {number} from 区域起点（含）
 * @property {number} to   区域终点（不含）
 * @property {string} text 公式全文（含定界符 / \begin…\end）
 * @property {boolean} display 是否为显示模式（决定 KaTeX displayMode）
 */

/* ── 扫描状态 ────────────────────────────────────────────────────────────── */

// math.type：inline（$…$）/ paren（\(…\)）/ displayDollar（$$…$$）/
// bracket（\[…\]）/ env（\begin{env}…\end{env}）
// depth 仅 env 使用：同名环境嵌套深度

/** 判断位置 i 处是否以某个显示环境 \begin 开头，返回环境名或 null。 */
function matchBeginEnv(text, i) {
  if (!text.startsWith("\\begin{", i)) return null;
  const nameStart = i + 7;
  const nameEnd = text.indexOf("}", nameStart);
  if (nameEnd === -1) return null;
  const name = text.slice(nameStart, nameEnd);
  return DISPLAY_ENV_SET.has(name) ? name : null;
}

/** 跳过注释：返回该行换行符之后的位置（或文本末尾）。 */
function skipToLineEnd(text, i) {
  const nl = text.indexOf("\n", i);
  return nl === -1 ? text.length : nl + 1;
}

/**
 * 状态机单步推进。
 * @returns {{i: number, math: object|null, closed: {from: number, to: number}|null}}
 *   closed 仅在一步内恰好闭合区域时非空。
 */
function step(text, i, math) {
  const ch = text[i];

  if (math) {
    switch (math.type) {
      case "inline": {
        if (ch === "\\") return { i: i + 2, math }; // \$ 等转义对（字面美元）
        if (ch === "$") {
          const closed = { from: math.from, to: i + 1 };
          return { i: i + 1, math: null, closed };
        }
        if (ch === "%") return { i: skipToLineEnd(text, i), math };
        return { i: i + 1, math };
      }
      case "displayDollar": {
        if (ch === "\\") return { i: i + 2, math };
        if (ch === "$" && text[i + 1] === "$") {
          const closed = { from: math.from, to: i + 2 };
          return { i: i + 2, math: null, closed };
        }
        if (ch === "%") return { i: skipToLineEnd(text, i), math };
        return { i: i + 1, math };
      }
      case "paren": {
        if (ch === "\\") {
          if (text[i + 1] === ")") {
            const closed = { from: math.from, to: i + 2 };
            return { i: i + 2, math: null, closed };
          }
          return { i: i + 2, math };
        }
        if (ch === "%") return { i: skipToLineEnd(text, i), math };
        return { i: i + 1, math };
      }
      case "bracket": {
        if (ch === "\\") {
          if (text[i + 1] === "]") {
            const closed = { from: math.from, to: i + 2 };
            return { i: i + 2, math: null, closed };
          }
          return { i: i + 2, math };
        }
        if (ch === "%") return { i: skipToLineEnd(text, i), math };
        return { i: i + 1, math };
      }
      case "env": {
        if (ch === "\\") {
          const envName = math.env;
          // \begin{env} = 7 + 环境名 + 1；\end{env} = 5 + 环境名 + 1
          if (text.startsWith(`\\begin{${envName}}`, i)) {
            return {
              i: i + envName.length + 8,
              math: { ...math, depth: math.depth + 1 },
            };
          }
          if (text.startsWith(`\\end{${envName}}`, i)) {
            const end = i + envName.length + 6;
            if (math.depth === 0) {
              const closed = { from: math.from, to: end };
              return { i: end, math: null, closed };
            }
            return {
              i: end,
              math: { ...math, depth: math.depth - 1 },
            };
          }
          return { i: i + 2, math };
        }
        if (ch === "%") return { i: skipToLineEnd(text, i), math };
        return { i: i + 1, math };
      }
    }
  }

  /* math === null：定界符外 */
  if (ch === "\\") {
    const next = text[i + 1];
    if (next === "(") return { i: i + 2, math: { type: "paren", from: i } };
    if (next === "[") return { i: i + 2, math: { type: "bracket", from: i } };
    const env = matchBeginEnv(text, i);
    if (env) {
      return {
        i: i + env.length + 8,
        math: { type: "env", env, from: i, depth: 0 },
      };
    }
    return { i: i + 2, math }; // 转义对：\$ \% \\ 等
  }
  if (ch === "$") {
    if (text[i + 1] === "$") {
      return { i: i + 2, math: { type: "displayDollar", from: i } };
    }
    return { i: i + 1, math: { type: "inline", from: i } };
  }
  if (ch === "%") return { i: skipToLineEnd(text, i), math };
  return { i: i + 1, math };
}

/** 悬停位置所在行内、位置之前是否存在未转义 `%`（用于注释内 opener 排除）。 */
function isInsideComment(text, pos) {
  const lineStart = text.lastIndexOf("\n", pos - 1) + 1;
  for (let i = lineStart; i < pos; i += 1) {
    if (text[i] !== "%") continue;
    let backslashes = 0;
    for (let j = i - 1; j >= lineStart && text[j] === "\\"; j -= 1) {
      backslashes += 1;
    }
    if (backslashes % 2 === 0) return true;
  }
  return false;
}

/** 位置 i 处的公式 opener（`$$` / `$` / `\(` / `\[` / 显示环境 begin）。 */
function matchOpenerAt(text, i) {
  const ch = text[i];
  if (ch === "$") {
    return text[i + 1] === "$"
      ? { math: { type: "displayDollar", from: i }, len: 2 }
      : { math: { type: "inline", from: i }, len: 1 };
  }
  if (ch === "\\" && text[i + 1] === "(") {
    return { math: { type: "paren", from: i }, len: 2 };
  }
  if (ch === "\\" && text[i + 1] === "[") {
    return { math: { type: "bracket", from: i }, len: 2 };
  }
  const env = matchBeginEnv(text, i);
  if (env) {
    return {
      math: { type: "env", env, from: i, depth: 0 },
      len: env.length + 8,
    };
  }
  return null;
}

/** 从 i 起继续扫描至闭合；返回闭合终点下标，未闭合返回 -1。 */
function scanToClose(text, i, math) {
  let cur = math;
  let cursor = i;
  while (cursor < text.length) {
    const r = step(text, cursor, cur);
    if (r.closed) return r.closed.to;
    cursor = r.i;
    cur = r.math;
  }
  return -1;
}

function makeRegion(text, info, to) {
  const display =
    info.type === "displayDollar" ||
    info.type === "bracket" ||
    info.type === "env";
  return { from: info.from, to, text: text.slice(info.from, to), display };
}

/**
 * 判定文档位置 pos 是否落在公式区域内。
 *
 * 区域包含判定：`from <= pos < to`（悬停在定界符任一字符上均命中）。
 * 命中返回 FormulaRegion；不在公式内 / 公式未闭合 / 位于注释行内返回 null。
 *
 * @param {string} text 文档全文
 * @param {number} pos 悬停位置
 * @returns {FormulaRegion|null}
 */
export function findFormulaAt(text, pos) {
  if (typeof text !== "string" || !Number.isInteger(pos)) return null;
  if (pos < 0 || pos > text.length) return null;

  let math = null;
  let i = 0;
  while (i < pos) {
    const prevMath = math;
    const r = step(text, i, math);
    if (r.closed) {
      // 闭合定界符跨越 pos（如 `\)` 的右括号恰为 pos）→ pos 仍在区域内
      if (r.closed.from <= pos && r.closed.to > pos) {
        return makeRegion(text, prevMath, r.closed.to);
      }
    }
    // 顶层转义对（\\、\$、\% 等）恰好跨过 pos：pos 是字面字符，
    // 不可能是公式 opener，直接判定不在公式内（否则 `\$` 的 `$` 或
    // `\\(` 的 `(` 会被误判为新公式起点）
    if (math === null && r.math === null && r.i > pos && text[i] === "\\") {
      return null;
    }
    i = r.i;
    math = r.math;
  }

  if (math) {
    const to = scanToClose(text, i, math);
    return to === -1 ? null : makeRegion(text, math, to);
  }

  // pos 恰好压在 opener 首字符上（如悬停行首 `$`）：该 opener 属于新区域
  if (pos < text.length && !isInsideComment(text, pos)) {
    const opener = matchOpenerAt(text, pos);
    if (opener) {
      const to = scanToClose(text, pos + opener.len, opener.math);
      return to === -1 ? null : makeRegion(text, opener.math, to);
    }
  }
  return null;
}
