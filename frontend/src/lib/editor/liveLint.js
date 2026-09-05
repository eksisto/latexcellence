/**
 * 实时语法诊断 CM6 lint 扩展（editor-experience-design §4.3 阶段三 / 决策 Q9）。
 *
 * `linter(..., { delay: 500 })`：编辑停顿约 500ms 后对缓冲区全文运行
 * diagnostics.computeDiagnostics（纯模块，规则与豁免口径见该模块头注），
 * 诊断以内联波浪线 + 悬停提示呈现（不进编译错误列表、不影响编译）。
 *
 * 诊断消息三语：computeDiagnostics 只产出 rule + params，本模块经注入的
 * translate（i18n.t 闭包，同 formulaHover / searchPanel 先例）按
 * `components:editor.diagnostics.*` 文案表组装 message——语言切换后
 * 下一次诊断运行即生效。
 *
 * 扩展对象由调用方（LaTeXEditor）按模块级单例创建（同 SELECTION_MATCHES
 * 先例）：liveDiagnostics 偏好开关经条件挂载 + tab 存档失配重建生效，
 * 本模块不感知偏好。
 */

import { linter } from "@codemirror/lint";

import {
  computeDiagnostics,
  DIAG_NOTE_TRUNCATED,
  DIAG_RULE_UNBALANCED_BRACES,
  DIAG_RULE_UNMATCHED_ENV,
} from "./diagnostics";

/** rule + params.kind → i18n 文案键（components:editor.diagnostics.*） */
const MESSAGE_KEYS = {
  [`${DIAG_RULE_UNMATCHED_ENV}:unclosed`]:
    "components:editor.diagnostics.envUnclosed",
  [`${DIAG_RULE_UNMATCHED_ENV}:stray`]:
    "components:editor.diagnostics.envStray",
  [`${DIAG_RULE_UNBALANCED_BRACES}:extra-close`]:
    "components:editor.diagnostics.braceExtraClose",
  [`${DIAG_RULE_UNBALANCED_BRACES}:unclosed`]:
    "components:editor.diagnostics.braceUnclosed",
  [DIAG_NOTE_TRUNCATED]: "components:editor.diagnostics.truncatedNote",
};

/**
 * 创建实时诊断 lint 扩展。
 * @param {object} options
 * @param {(key: string, params?: object) => string} options.translate
 *   i18n 翻译函数（i18n.t 闭包；每次诊断运行时求值，语言切换即时生效）
 * @returns {object} CM6 Extension（linter，delay 500ms）
 */
export function createDiagnosticsLinter({ translate }) {
  const formatMessage = (rule, params) => {
    const key =
      (params && params.kind ? MESSAGE_KEYS[`${rule}:${params.kind}`] : null) ??
      MESSAGE_KEYS[rule] ??
      rule;
    return translate(key, params);
  };
  return linter(
    (view) =>
      computeDiagnostics(view.state.doc.toString(), formatMessage).map((d) => ({
        from: d.from,
        to: d.to,
        severity: d.severity,
        message: d.message,
      })),
    { delay: 500 },
  );
}
