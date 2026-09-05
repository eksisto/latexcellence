/**
 * 实时语法诊断纯模块（editor-experience-design §4.3 阶段三 / 决策 Q9）。
 *
 * 输入缓冲区全文，输出诊断数组
 * `{ from, to, severity, rule, params, message }`：
 *   - from/to：CM6 文档偏移（诊断下划线区间）；
 *   - severity："error"（unmatched-env）| "warning"（unbalanced-braces）；
 *   - rule + params：结构化规则标识与定位参数（三语文案表的格式化输入）；
 *   - message：经注入的 formatMessage(rule, params) 组装（接线层传 i18n
 *     文案表；缺省回落内置英文兜底文案，保证纯模块 node --test 可测且
 *     不依赖 i18n 运行时）。
 *
 * 规则两条（Q9）：
 *   1. `unmatched-env`（error）：`\begin`/`\end` 栈匹配——环境名扫描思路
 *      与 formulaHoverScan 同源（startsWith + 定界查找，无嵌套量词正则）。
 *      `\end{env}` 与栈顶同名弹出；命中更深层同名时其上内层环境全部按
 *      未闭合报告并整段弹出（LaTeX 要求环境正确嵌套）；栈内无同名报
 *      stray `\end`；文档末尾栈内残留按未闭合报告（定位在各 `\begin`
 *      token 区间）。verbatim 类环境整体豁免（与 outlineScan 同一保守
 *      清单）：内容全字面（% 注释、花括号、\begin/\end 均为普通文本），
 *      只找 `\end{同名}` 结束；未闭合的 verbatim 环境本身仍在文末报告，
 *      其后内容随之整体字面化（天然抑制级联误报）。
 *   2. `unbalanced-braces`（warning）：全文档花括号栈计数——多余的 `}`
 *      报告所在行（每行最多一条，定位到失衡行）；文末栈内残留报告在最
 *      内层未闭合 `{` 上（params.depth = 未闭合总数）。豁免口径：
 *        - 注释：非转义 `%`（含 `\%` / `\\%` 奇偶判定）之后到行尾，
 *          与 outlineScan.stripComment 同源；
 *        - `\verb` / `\verb*` 行内参数（outlineScan 同口径：定界符为
 *          字母/空白视为非法形态不豁免；参数不允许跨行）；
 *        - 转义 `\{` / `\}`（泛化为 `\X` 转义对整体跳过）；
 *        - URL 字面量：`\url` / `\path` / `\href` 的第一个花括号参数，
 *          与后端 latex_literals.py 同源——参数内 `%`（%20 等）为字面
 *          字符不再起注释，花括号按转义感知配对整体吞掉（参数未闭合时
 *          余下内容整体按 URL 字面豁免）。
 *
 * 截断：诊断超过 DIAGNOSTICS_LIMIT 条时按文档位置排序截断，并在首条
 * 标注（truncated/total 字段 + message 附加截断提示，提示文案同样经
 * formatMessage 的 DIAG_NOTE_TRUNCATED 规则组装）。
 *
 * 性能：单向单遍字符扫描，整体 O(n)——诊断产生时只做 O(1) 记账（行号经
 * 跳转增量维护，不做逐诊断回扫）；`\end` 的栈内同名查找由「环境名 → 栈内
 * 出现次数」索引先行 O(1) 判空，杜绝「深环境栈 × 海量 stray `\end`」的
 * 平方退化（命中后的线性扫描成本由弹出摊还）；超限且双栈皆空后进入排水
 * 模式，余下诊断只计数不构建条目（total 保持精确）；消息文案只对截断后
 * 返回的 ≤ 200 条组装（病态大文档不产生海量 formatMessage/i18n 调用）；
 * 追加顺序已非降时跳过排序。linter 回调运行在主线程，这些护栏是硬性要求。
 *
 * 纯函数（零外部依赖），供 node --test 直接单测
 * （test/editorDiagnostics.test.mjs）。
 */

/** 诊断条数上限（超出按文档位置截断，首条标注） */
export const DIAGNOSTICS_LIMIT = 200;

/** 规则标识：\begin/\end 栈匹配（severity error） */
export const DIAG_RULE_UNMATCHED_ENV = "unmatched-env";

/** 规则标识：花括号不配平（severity warning） */
export const DIAG_RULE_UNBALANCED_BRACES = "unbalanced-braces";

/** 截断标注规则标识（非独立诊断，仅用于向 formatMessage 取提示文案） */
export const DIAG_NOTE_TRUNCATED = "truncated";

/** verbatim 类环境保守清单（与 outlineScan.js 同一集合） */
const VERBATIM_ENVS = new Set([
  "verbatim",
  "verbatim*",
  "Verbatim",
  "Verbatim*",
  "lstlisting",
  "minted",
]);

function isLetter(ch) {
  return (ch >= "a" && ch <= "z") || (ch >= "A" && ch <= "Z");
}

function isSpace(ch) {
  return ch === " " || ch === "\t";
}

/** 统计 text[from, to) 内换行符个数（跳转增量维护行号用） */
function countNewlines(text, from, to) {
  let count = 0;
  for (let k = from; k < to; k += 1) {
    if (text.charCodeAt(k) === 10) count += 1;
  }
  return count;
}

/**
 * 缺省消息格式化（接线层未注入 formatMessage 时的英文兜底文案；
 * 生产路径由 liveLint.js 注入 i18n 三语文案表）。
 * @param {string} rule
 * @param {Record<string, unknown>} params
 * @returns {string}
 */
function defaultFormatMessage(rule, params = {}) {
  if (rule === DIAG_RULE_UNMATCHED_ENV) {
    return params.kind === "stray"
      ? `\\end{${params.env}} has no matching \\begin`
      : `\\begin{${params.env}} is never closed`;
  }
  if (rule === DIAG_RULE_UNBALANCED_BRACES) {
    return params.kind === "extra-close"
      ? `unexpected } near line ${params.line}`
      : `{ on line ${params.line} is never closed`;
  }
  if (rule === DIAG_NOTE_TRUNCATED) {
    return `showing first ${params.limit} of ${params.total} diagnostics`;
  }
  return rule;
}

/**
 * 跳过 `\verb` / `\verb*` 行内参数（outlineScan.stripVerbSpans 同口径）。
 * @param {string} text
 * @param {number} j 命令名 "verb" 之后的下标
 * @returns {number} 扫描继续位置
 */
function skipVerbSpan(text, j) {
  let k = j;
  if (text[k] === "*") k += 1;
  const delim = text[k];
  if (delim === undefined || isLetter(delim) || isSpace(delim)) {
    return k; // 非法 verb 形态（定界符缺失/为字母/空白）：跳过命令名按普通文本继续
  }
  const nl = text.indexOf("\n", k + 1);
  const end = text.indexOf(delim, k + 1);
  if (end === -1 || (nl !== -1 && end > nl)) {
    // 未闭合：\verb 参数不允许跨行，本行余下内容整体视为 verb 内部排除
    return nl === -1 ? text.length : nl;
  }
  return end + 1;
}

/**
 * 按 URL 字面消费 `\url` / `\path` / `\href` 的第一个花括号参数
 * （后端 latex_literals.py 同源口径）：可选空白 + 可选 `[...]` + `{`，
 * 参数内 `%` 不起注释、花括号按转义感知配对整体吞掉；参数未闭合时
 * 余下内容整体按 URL 字面豁免。
 * @param {string} text
 * @param {number} j 命令名之后的下标
 * @returns {number} 扫描继续位置
 */
function skipUrlArgument(text, j) {
  let k = j;
  while (k < text.length && isSpace(text[k])) k += 1;
  if (text[k] === "[") {
    const nl = text.indexOf("\n", k + 1);
    const close = text.indexOf("]", k + 1);
    if (close === -1 || (nl !== -1 && close > nl)) {
      return nl === -1 ? text.length : nl; // 可选参数未在本行闭合：按普通文本继续
    }
    k = close + 1;
    while (k < text.length && isSpace(text[k])) k += 1;
  }
  if (text[k] !== "{") return k;
  let depth = 0;
  for (let p = k; p < text.length; p += 1) {
    const c = text[p];
    if (c === "\\") {
      p += 1; // \{ / \} 等转义对不参与配平（与 latex_literals.find_matching_brace 一致）
      continue;
    }
    if (c === "{") depth += 1;
    else if (c === "}") {
      depth -= 1;
      if (depth === 0) return p + 1;
    }
  }
  return text.length;
}

/**
 * 在 verbatim 类环境内容中查找 `\end{envName}`（内容全字面：只认环境
 * 结束标记，注释/花括号/\begin 均为普通文本）。
 * @param {string} text
 * @param {number} from 环境开始 token 之后的下标
 * @param {string} envName
 * @returns {number} 结束 token 之后的下标；未找到返回 -1
 */
function findVerbatimEnd(text, from, envName) {
  let pos = from;
  for (;;) {
    const idx = text.indexOf("\\end", pos);
    if (idx === -1) return -1;
    const after = text[idx + 4];
    if (after === undefined || !isLetter(after)) {
      let k = idx + 4;
      while (k < text.length && isSpace(text[k])) k += 1;
      if (text.startsWith(`{${envName}}`, k)) {
        return k + envName.length + 2;
      }
    }
    pos = idx + 1;
  }
}

/**
 * 扫描缓冲区全文，产出实时语法诊断（按文档位置升序，超限截断）。
 * @param {string} docText 缓冲区全文（`\n` 换行）
 * @param {(rule: string, params: Record<string, unknown>) => string} [formatMessage]
 *   诊断消息格式化器（接线层注入 i18n 三语文案表；缺省英文兜底）
 * @returns {Array<{from: number, to: number, severity: "error"|"warning",
 *   rule: string, params: object, message: string, truncated?: boolean, total?: number}>}
 */
export function computeDiagnostics(docText, formatMessage) {
  const format =
    typeof formatMessage === "function" ? formatMessage : defaultFormatMessage;
  /** @type {Array} */
  const items = [];
  if (typeof docText !== "string" || docText.length === 0) return items;

  const text = docText;
  let i = 0;
  let line = 1;
  /** 跳转推进：行号随跳转区间增量维护，诊断产生时 O(1) 取行 */
  const moveTo = (next) => {
    if (next > i) {
      line += countNewlines(text, i, next);
      i = next;
    }
  };

  /** 环境栈：\begin{name} 未遇配对 \end 时保留至文末统一报告 */
  const envStack = [];
  /** 栈内容名字计数（name → 出现次数）：stray 判定先走 O(1) 查表，
      防深栈 × 海量不匹配 \end 的平方退化；命中后的线性扫描由弹出摊还 */
  const envNameCounts = new Map();
  const countDec = (name) => {
    const left = envNameCounts.get(name);
    if (left > 1) envNameCounts.set(name, left - 1);
    else envNameCounts.delete(name);
  };

  /** 花括号栈：未闭合 `{` 的位置与所在行 */
  const braceStack = [];
  /** 花括号失衡诊断已覆盖的行（每行最多一条，定位到失衡行防连发噪声） */
  const braceReportedLines = new Set();

  let lastFrom = -1;
  let needsSort = false;
  /** 追加诊断：from 非降则免排序（仅弹出回溯/文末收尾会产生乱序追加） */
  const appendItem = (item) => {
    if (item.from < lastFrom) needsSort = true;
    else lastFrom = item.from;
    items.push(item);
  };

  /**
   * 「排水」模式：诊断条数已超限且环境栈/花括号栈皆空时，余下诊断的
   * from 必大于已收全部条目（扫描位置单调前进），截断后不可能入选，
   * 且文末收尾不可能再产生诊断——故只计数（skipped 保持 total 精确）
   * 不再构建条目，防病态大文档上的无效对象构建。
   */
  let drain = false;
  let skipped = 0;

  const makeItem = (rule, severity, from, to, params) => ({
    from,
    to,
    severity,
    rule,
    params,
    // message 延迟到截断后统一组装（见函数末尾），避免病态大文档上
    // 每诊断一次 formatMessage/i18n 调用
  });

  const reportUnclosedEnv = (entry) => {
    if (drain) {
      skipped += 1;
      return;
    }
    appendItem(
      makeItem(DIAG_RULE_UNMATCHED_ENV, "error", entry.from, entry.to, {
        kind: "unclosed",
        env: entry.name,
        line: entry.line,
      }),
    );
  };

  const reportBraceWarning = (pos, kind, line, depth) => {
    if (braceReportedLines.has(line)) return;
    braceReportedLines.add(line);
    if (drain) {
      skipped += 1;
      return;
    }
    const params = kind === "unclosed" ? { kind, line, depth } : { kind, line };
    appendItem(
      makeItem(DIAG_RULE_UNBALANCED_BRACES, "warning", pos, pos + 1, params),
    );
  };

  /** \begin{name} 入栈（同步维护名字计数索引） */
  const pushEnv = (entry) => {
    envStack.push(entry);
    envNameCounts.set(entry.name, (envNameCounts.get(entry.name) ?? 0) + 1);
  };

  /** 弹出 idx 之上（含 idx 本体，由本次 \end 闭合）的栈段并同步计数索引 */
  const truncateEnvStack = (idx) => {
    for (let p = envStack.length - 1; p >= idx; p -= 1) {
      countDec(envStack[p].name);
    }
    envStack.length = idx;
  };

  while (i < text.length) {
    if (
      !drain &&
      items.length > DIAGNOSTICS_LIMIT &&
      envStack.length === 0 &&
      braceStack.length === 0
    ) {
      drain = true; // 截断已注定：余下诊断只计数，total 经 skipped 保持精确
    }
    const ch = text[i];

    if (ch === "\n") {
      line += 1;
      i += 1;
      continue;
    }

    // 注释：非转义 % 到行尾整体跳过（\% 已被转义对分支消费，不会进入此分支）
    if (ch === "%") {
      const nl = text.indexOf("\n", i);
      moveTo(nl === -1 ? text.length : nl + 1);
      continue;
    }

    if (ch === "{") {
      braceStack.push({ pos: i, line });
      i += 1;
      continue;
    }

    if (ch === "}") {
      if (braceStack.length > 0) braceStack.pop();
      else reportBraceWarning(i, "extra-close", line);
      i += 1;
      continue;
    }

    if (ch === "\\") {
      const next = text[i + 1];
      if (next === undefined) {
        i += 1; // 文档末尾孤立反斜杠
        continue;
      }
      if (!isLetter(next)) {
        i += 2; // 转义对：\\ \% \{ \} \$ 等（字面字符，不参与配平/注释）
        continue;
      }
      // 读取完整命令名（字母序列）：杜绝 \urlstyle 误命中 \url、\endnote 误命中 \end
      let j = i + 1;
      while (j < text.length && isLetter(text[j])) j += 1;
      const name = text.slice(i + 1, j);

      if (name === "verb") {
        moveTo(skipVerbSpan(text, j));
        continue;
      }
      if (name === "url" || name === "path" || name === "href") {
        moveTo(skipUrlArgument(text, j));
        continue;
      }
      if (name === "begin" || name === "end") {
        let k = j;
        while (k < text.length && isSpace(text[k])) k += 1;
        if (text[k] !== "{") {
          moveTo(j); // 缺参数：命令名之后按普通文本扫描（花括号照常计数）
          continue;
        }
        // 环境名不允许跨行：同一行内找 }
        let close = -1;
        let nlAtName = -1;
        for (let p = k + 1; p < text.length; p += 1) {
          const c = text[p];
          if (c === "}") {
            close = p;
            break;
          }
          if (c === "\n") {
            nlAtName = p;
            break;
          }
        }
        if (close === -1) {
          // 环境名本行未闭合（畸形或键入中途）：按已敲出的环境名前缀继续
          // 栈匹配（键入过程不闪报），花括号随 token 消费、不重复计数；
          // 空前缀（如仅 `\begin{`）整体跳过
          const tokenTo = nlAtName === -1 ? text.length : nlAtName + 1;
          const partial = text.slice(
            k + 1,
            nlAtName === -1 ? text.length : nlAtName,
          );
          if (partial.length > 0) {
            if (name === "begin") {
              pushEnv({ name: partial, from: i, to: tokenTo, line });
            } else {
              // 静默匹配：命中则整段弹出（内层未闭合照常报告），
              // 未命中不报 stray（键入中途不闪报）；查表 O(1) 判空
              let pIdx = -1;
              if (envNameCounts.has(partial)) {
                for (let p = envStack.length - 1; p >= 0; p -= 1) {
                  if (envStack[p].name === partial) {
                    pIdx = p;
                    break;
                  }
                }
              }
              if (pIdx !== -1) {
                for (let p = envStack.length - 1; p > pIdx; p -= 1) {
                  reportUnclosedEnv(envStack[p]);
                }
                truncateEnvStack(pIdx);
              }
            }
          }
          moveTo(tokenTo);
          continue;
        }
        const envName = text.slice(k + 1, close);
        if (envName.length === 0) {
          moveTo(close + 1); // 空环境名 \begin{}：整体吞掉不计
          continue;
        }
        const tokenTo = close + 1;
        if (name === "begin") {
          pushEnv({ name: envName, from: i, to: tokenTo, line });
          if (VERBATIM_ENVS.has(envName)) {
            const endPos = findVerbatimEnd(text, tokenTo, envName);
            if (endPos === -1) {
              // verbatim 未闭合：余下内容全部字面（天然抑制级联误报），
              // 环境留待文末按未闭合报告
              moveTo(text.length);
            } else {
              countDec(envName);
              envStack.pop();
              moveTo(endPos);
            }
            continue;
          }
          moveTo(tokenTo);
          continue;
        }
        // \end{env}：与栈顶同名弹出；命中更深层同名时其上内层环境全部
        // 未闭合（LaTeX 要求正确嵌套），整段弹出；栈内无同名报 stray
        // （名字计数索引 O(1) 判空，线性扫描仅在确有同名时进行、成本由
        // 弹出摊还）
        let idx = -1;
        if (envNameCounts.has(envName)) {
          for (let p = envStack.length - 1; p >= 0; p -= 1) {
            if (envStack[p].name === envName) {
              idx = p;
              break;
            }
          }
        }
        if (idx === -1) {
          if (drain) {
            skipped += 1;
          } else {
            appendItem(
              makeItem(DIAG_RULE_UNMATCHED_ENV, "error", i, tokenTo, {
                kind: "stray",
                env: envName,
                line,
              }),
            );
          }
        } else {
          // 只报告 idx 之上（真正未闭合）的内层环境；idx 本体由本次 \end 闭合
          for (let p = envStack.length - 1; p > idx; p -= 1) {
            reportUnclosedEnv(envStack[p]);
          }
          truncateEnvStack(idx);
        }
        moveTo(tokenTo);
        continue;
      }
      // 其他命令：跳过命令名（其后的花括号参数照常计数）
      i = j;
      continue;
    }

    i += 1;
  }

  /* ── 文末收尾：栈内残留 = 未闭合 ────────────────────────────────────── */
  for (const entry of envStack) reportUnclosedEnv(entry);
  if (braceStack.length > 0) {
    const top = braceStack[braceStack.length - 1];
    reportBraceWarning(top.pos, "unclosed", top.line, braceStack.length);
  }

  /* ── 按文档位置排序（追加已非降则免） + 上限截断（首条标注） ──────────── */
  if (needsSort) items.sort((a, b) => a.from - b.from || a.to - b.to);
  const total = items.length + skipped;
  const kept =
    total > DIAGNOSTICS_LIMIT ? items.slice(0, DIAGNOSTICS_LIMIT) : items;
  // 消息延后组装：只对返回条目调 format（病态大文档不产生海量 i18n 调用）
  const out = kept.map((d) => ({
    from: d.from,
    to: d.to,
    severity: d.severity,
    rule: d.rule,
    params: d.params,
    message: format(d.rule, d.params),
  }));
  if (total > DIAGNOSTICS_LIMIT) {
    out[0].truncated = true;
    out[0].total = total;
    out[0].message += `\n${format(DIAG_NOTE_TRUNCATED, {
      limit: DIAGNOSTICS_LIMIT,
      total,
    })}`;
  }
  return out;
}
