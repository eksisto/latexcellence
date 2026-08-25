/**
 * 行内补全（editor-plan §6.3 入口一 / 决策 Q8、Q15）。
 *
 * 双触发：
 *   - `\` 触发命令补全（反斜杠后可继续输入英文字母或中文，中文走共享
 *     匹配器的拼音/子串路）；
 *   - `\begin{` 触发环境名补全，应用时插入含 `\end{}` 的完整环境模板，
 *     自动配对（决策 Q15：\ref/\cite 留二期，不在本 source 范围）。
 *
 * 一致性约束（§6.2）：命令补全与命令面板共用 lib/editor/matcher.js 的
 * 同一匹配索引与打分；环境名补全复用同一 matchScore 打分函数。
 * 命中项经 snippetInsert.insertSnippet 应用，占位符跳转模式行为一致。
 */

import { autocompletion } from "@codemirror/autocomplete";

import { LATEX_COMMANDS } from "../../data/latexCommands/index.js";
import {
  getCommandMatcherIndex,
  buildCommandIndex,
  listCommands,
  searchCommands,
} from "./matcher.js";
import { expandEntryInsert, insertSnippet } from "./snippetInsert.js";

/* ── 环境清单：从数据库条目的 \begin{X} 提取（含 align* 等带星形态） ─── */

const BEGIN_ENV_RE = /\\begin\{([A-Za-z*]+)\}/;

/** @returns {{name:string, entry:object}[]} 数据库中定义的环境去重清单 */
export function extractEnvironments(commands = LATEX_COMMANDS) {
  const seen = new Map();
  for (const entry of commands) {
    const m = BEGIN_ENV_RE.exec(entry?.insert ?? "");
    if (!m) continue;
    const name = m[1];
    // 同名环境取首条（如 determinant 复用 vmatrix 骨架，不重复收录）
    if (!seen.has(name)) seen.set(name, { name, entry });
  }
  return [...seen.values()];
}

/** 环境名索引（ASCII 名称，无需拼音；复用同一打分函数） */
let envIndex = null;
function getEnvIndex() {
  if (!envIndex) {
    envIndex = buildCommandIndex(
      extractEnvironments().map(({ name, entry }) => ({
        ...entry,
        name,
      })),
    );
  }
  return envIndex;
}

/* ── 光标前缀解析 ─────────────────────────────────────────────────────── */

/** `\` 后的命令词（字母或中文；中文供拼音三路查询） */
const COMMAND_PREFIX_RE = /\\([A-Za-z\u4e00-\u9fff]*)$/;
/** `\begin{` 后的部分环境名 */
const BEGIN_PREFIX_RE = /\\begin\{([A-Za-z]*)$/;

function textBefore(state, pos) {
  const line = state.doc.lineAt(pos);
  return line.text.slice(0, pos - line.from);
}

/* ── 补全 source ──────────────────────────────────────────────────────── */

/**
 * 命令补全 source。异步等待共享索引（首次动态加载 pinyin-pro，
 * 之后命中缓存），四路匹配与命令面板完全一致。
 * @param {CompletionContext} context CM6 补全上下文
 * @param {((entry: object) => void)|undefined} onApplyEntry 应用后回调（宏包缺失联动）
 */
function makeCommandCompletionSource(onApplyEntry) {
  return async function commandCompletionSource(context) {
    if (context.state.readOnly) return null;
    const before = textBefore(context.state, context.pos);
    const m = COMMAND_PREFIX_RE.exec(before);
    if (!m) return null;
    const query = m[1];
    const from = context.pos - query.length;
    const index = await getCommandMatcherIndex();
    // 空查询（刚敲下 `\`）按数据库默认顺序给默认列表，与面板初始视图一致
    const entries = query ? searchCommands(index, query) : listCommands(index);
    if (entries.length === 0) return null;
    return {
      from,
      options: entries.map((entry) => ({
        label: entry.name,
        detail: entry.zh,
        boost: 0,
        apply: (view, _completion, from2, to) => {
          insertSnippet(view, expandEntryInsert(entry), { from: from2, to });
          onApplyEntry?.(entry);
        },
      })),
      validFor: /^[A-Za-z\u4e00-\u9fff]*$/,
    };
  };
}

/**
 * 环境名补全 source：`\begin{` 触发；应用时整体替换
 * `\begin{部分输入}` 为完整环境模板（含自动配对的 `\end{}`）。
 */
function makeEnvironmentCompletionSource(onApplyEntry) {
  return function environmentCompletionSource(context) {
    if (context.state.readOnly) return null;
    const before = textBefore(context.state, context.pos);
    const m = BEGIN_PREFIX_RE.exec(before);
    if (!m) return null;
    const query = m[1];
    const from = context.pos - query.length - "\\begin{".length;
    // 空查询（刚敲完 `\begin{`）按数据库默认顺序给出全部环境
    const matches = query
      ? searchCommands(getEnvIndex(), query, { limit: 30 })
      : listCommands(getEnvIndex(), 30);
    if (matches.length === 0) return null;
    return {
      from,
      options: matches.map((entry) => {
        const envName = BEGIN_ENV_RE.exec(entry.insert)?.[1] ?? entry.name;
        return {
          label: envName,
          detail: entry.zh,
          boost: 0,
          apply: (view, _completion, from2, to) => {
            insertSnippet(view, expandEntryInsert(entry), { from: from2, to });
            onApplyEntry?.(entry);
          },
        };
      }),
      validFor: /^[A-Za-z]*$/,
    };
  };
}

/**
 * LaTeX 行内补全扩展：命令 + 环境双 source。
 * viewer 只读态由各 source 内部短路（editable=false 本就不触发）。
 * @param {{onApplyEntry?: (entry: object) => void}} [options]
 *   应用补全后回调（EditorPage 接宏包缺失联动；决策 Q14 与面板行为一致）
 * @returns {import("@codemirror/state").Extension}
 */
export function latexCompletionExtension({ onApplyEntry } = {}) {
  return autocompletion({
    override: [
      makeEnvironmentCompletionSource(onApplyEntry),
      // 异步 source 与同步 source 混排，CM6 原生支持 Promise 结果
      makeCommandCompletionSource(onApplyEntry),
    ],
    icons: false,
    closeOnBlur: true,
    defaultKeymap: true,
  });
}
