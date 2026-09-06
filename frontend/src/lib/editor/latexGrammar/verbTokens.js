/**
 * \verb 行内参数外部分词器（editor v3 阶段六a / 决策 Q14）。
 *
 * `\verb` 的定界符是「任意非字母非空白字符，且两侧相同」（`\verb|x|`、
 * `\verb!..!`、`\verb*#..#` 等），CFG 无法表达「同字符闭合」，故由本外部
 * token 实现：在 `\verb` / `\verb*` 命令名之后被调用（grammar 中仅
 * VerbCommand 的 verbArg 位置引用，因此只在合法状态被触发）。
 *
 * 口径与 outlineScan.stripVerbSpans 一致：
 *   - 首字符为字母或空白（定界符非法）→ 不产出 token，交由错误恢复；
 *   - 未闭合（定界符直到行尾未重现）→ 吞到本行行尾为止（不含换行符），
 *     避免 `\verb|未闭合` 把整份余下文档拖入原样态；文档末行视为行尾；
 *   - 参数内容（含首尾定界符）整体作为一个 verbArg token，原文零语义。
 *
 * 必须以 `{ fallback: true }` 声明：@lezer/lr 的 token 读取循环在更高优先
 * 的分词器（主 token 表）产出任意 token（含对当前状态非法者，如 `|` 命中
 * 的 plainChar）后即停止，非 fallback 的外部分词器不再被询问——verbArg
 * 将永远无法产出。fallback 语义（先前 token 与当前状态动作均不匹配时才
 * 询问本分词器）恰为 verbArg 所需：定界符字符在主表中总是匹配 plainChar，
 * 但仅在 `\verb` 参数位置可被移进。
 *
 * 本模块依赖生成产物 parser.terms.js 的 token id（生成物不入库，经
 * latexGrammar/build.mjs 在 dev/build/test 前生成）。
 */

import { ExternalTokenizer } from "@lezer/lr";
import { verbArg } from "./parser.terms.js";

export const verbReader = new ExternalTokenizer(
  (input) => {
    const delim = input.next;
    if (delim < 0) return;
    // 定界符非法（字母 / 空白）：与 outlineScan 同口径放弃识别
    const ch = String.fromCharCode(delim);
    if (/[a-zA-Z\s]/.test(ch)) return;
    for (;;) {
      input.advance();
      if (input.next < 0) break; // EOF（文档末行行尾）：吞到行尾为止
      if (input.next === 10) break; // 换行：未闭合，吞到行尾为止（不含换行）
      if (input.next === delim) {
        input.advance(); // 闭合定界符并入 token
        break;
      }
    }
    input.acceptToken(verbArg);
  },
  { fallback: true },
);
