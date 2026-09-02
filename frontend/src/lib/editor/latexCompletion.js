/**
 * 行内补全（editor-plan §6.3 入口一 / 决策 Q8、Q15）。
 *
 * 双触发：
 *   - `\` 触发命令补全（反斜杠后可继续输入英文字母或中文，中文走共享
 *     匹配器的拼音/子串路）；
 *   - `\begin{` 触发环境名补全，应用时插入含 `\end{}` 的完整环境模板，
 *     自动配对（决策 Q15：\ref/\cite 留二期，不在本 source 范围）；
 *   - `\ref{` / `\label{` / `\cite{` 触发引用补全（editor v2 阶段四 /
 *     决策 Q7），数据来自 makeReferenceCompletionSource 注入的项目符号
 *     缓存（lib/editor/latexSymbols.js）。
 *
 * 一致性约束（§6.2）：命令补全与命令面板共用 lib/editor/matcher.js 的
 * 同一匹配索引与打分；环境名补全复用同一 matchScore 打分函数。
 * 命中项经 snippetInsert.insertSnippet 应用，占位符跳转模式行为一致。
 * 弹层外观由 completionTheme.js 定义（学术索引卡：命令名 ····· 释义）。
 * 中文名 detail 按界面语言本地化（commandZh.js）：简中原样、繁体转换、
 * 英文界面隐藏；当前语言由调用方经 getLanguage 注入（本模块不依赖
 * 应用级 i18n 单例，node --test 可直接导入）。
 */

import { autocompletion } from "@codemirror/autocomplete";

import { LATEX_COMMANDS } from "../../data/latexCommands/index.js";
import {
  getCommandMatcherIndex,
  buildCommandIndex,
  listCommands,
  searchCommands,
} from "./matcher.js";
import { getCommandZhDisplay } from "./commandZh.js";
import { completionPopupTheme } from "./completionTheme.js";
import { expandEntryInsert, insertSnippet } from "./snippetInsert.js";
import {
  buildCiteOptions,
  buildLabelOptions,
  filterCompletionOptions,
} from "./latexSymbols.js";

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

/* ── 引用补全触发（editor-productivity-plan §5.3 阶段四 / 决策 Q7） ───
 * 三路触发共用同一 source（触发词互斥）：
 *   - \ref{ / \cref{ / \Cref{ / \eqref{ / \pageref{ / \autoref{（含星号与
 *     已敲出的可选参数）：补全全项目已有 label（跨文件命中，Q7）；
 *   - \label{：同样补全已有 label（提示既有定义，避免重复标签）；
 *   - \cite 家族（\cite/\citep/\citet/\parencite/\textcite/...，含星号与
 *     可选参数如 [p.~5]）：补全 bib 条目 key 与 .tex 已引用 key。
 * key 字符集与 validFor 对齐：字母数字 + : _ - . */
const REF_TRIGGER_RE =
  /\\(?:[cC]|eq|page|auto)?ref\*?(?:\[[^\][{}]*\])*\{([A-Za-z0-9_:.-]*)$/;
const LABELDEF_TRIGGER_RE = /\\label\{([A-Za-z0-9_:.-]*)$/;
const CITE_TRIGGER_RE =
  /\\[A-Za-z]*[cC]ite[A-Za-z]*\*?(?:\[[^\][{}]*\])*\{([A-Za-z0-9_:.-]*)$/;
const SYMBOL_KEY_VALID_FOR = /^[A-Za-z0-9_:.-]*$/;

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
 * @param {() => string} getLanguage 当前界面语言读取器（每次触发时求值）
 */
export function makeCommandCompletionSource(onApplyEntry, getLanguage) {
  return async function commandCompletionSource(context) {
    if (context.state.readOnly) return null;
    const before = textBefore(context.state, context.pos);
    const m = COMMAND_PREFIX_RE.exec(before);
    if (!m) return null;
    const query = m[1];
    const from = context.pos - query.length;
    // 匹配索引（pinyin-pro chunk）与中文名转换器（opencc-js chunk）相互
    // 独立，并行加载：串行 await 会让 zh-TW 界面首次触发补全的弹层延迟
    // 变成两个 chunk 下载时间之和（async-parallel）
    const [index, displayZh] = await Promise.all([
      getCommandMatcherIndex(),
      getCommandZhDisplay(getLanguage()),
    ]);
    // 空查询（刚敲下 `\`）按数据库默认顺序给默认列表，与面板初始视图一致
    const entries = query ? searchCommands(index, query) : listCommands(index);
    if (entries.length === 0) return null;
    return {
      from,
      options: entries.map((entry) => ({
        label: entry.name,
        detail: displayZh ? displayZh(entry.zh) : undefined,
        boost: 0,
        apply: (view, _completion, from2, to) => {
          const template = expandEntryInsert(entry);
          // CM6 传入的 [from2, to] 只覆盖 `\` 之后的命令词（result.from 即
          // source 返回的 from），而模板自带前导 `\`，须左移一格把已敲入的
          // `\` 一并替换，否则应用后得到 `\\alpha` 这类双反斜杠；
          // from2 前一字符不是 `\`（弹层存续期间被删除）时回退原区间
          const charBefore =
            from2 > 0 ? view.state.doc.sliceString(from2 - 1, from2) : "";
          insertSnippet(view, template, {
            from:
              template.startsWith("\\") && charBefore === "\\"
                ? from2 - 1
                : from2,
            to,
          });
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
 * （两个 source 工厂均导出：node --test 经 mock view 锁应用区间契约）
 */
export function makeEnvironmentCompletionSource(onApplyEntry, getLanguage) {
  return async function environmentCompletionSource(context) {
    if (context.state.readOnly) return null;
    const before = textBefore(context.state, context.pos);
    const m = BEGIN_PREFIX_RE.exec(before);
    if (!m) return null;
    const query = m[1];
    const from = context.pos - query.length - "\\begin{".length;
    // 空查询（刚敲完 `\begin{`）按数据库默认顺序给出全部环境
    const matches = query
      ? searchCommands(getEnvIndex(), query, { limit: 30 })
      : listCommands(getEnvIndex(), { limit: 30 });
    if (matches.length === 0) return null;
    const displayZh = await getCommandZhDisplay(getLanguage());
    return {
      from,
      options: matches.map((entry) => {
        // 环境名在 getEnvIndex() 构建期已从 \begin{X} 提取并覆盖进
        // entry.name，无需每键对候选项重新 exec（纯冗余计算）
        return {
          label: entry.name,
          detail: displayZh ? displayZh(entry.zh) : undefined,
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
 * 引用补全 source（阶段四 / Q7）：`\ref{` / `\label{` / `\cite{` 三路
 * 触发，数据来自注入的 getRefSymbols()（lib/editor/latexSymbols.js 的
 * 项目符号缓存，缓存缺失时自动拉取，loading 期间走 CM6 默认行为）。
 * 应用补全只替换 `{` 之后已敲入的部分 key（`\ref{` 前缀原样保留），
 * 无需自定义 apply。
 * @param {(() => Promise<object|null>)|undefined} getRefSymbols
 *   当前项目符号数据读取器（项目未打开返回 null → source 短路）
 */
export function makeReferenceCompletionSource(getRefSymbols) {
  return async function referenceCompletionSource(context) {
    if (context.state.readOnly) return null;
    if (typeof getRefSymbols !== "function") return null;
    const before = textBefore(context.state, context.pos);
    let kind = null;
    let m = REF_TRIGGER_RE.exec(before) || LABELDEF_TRIGGER_RE.exec(before);
    if (m) kind = "label";
    else {
      m = CITE_TRIGGER_RE.exec(before);
      if (m) kind = "cite";
    }
    if (!m) return null;
    const query = m[1];
    const data = await getRefSymbols();
    if (!data) return null;
    // 过滤排序后仅保留 query 替换区间（from 在 `{` 之后），应用时
    // CM6 默认行为把 [from, pos] 替换为选中项的 label
    const options = filterCompletionOptions(
      kind === "label" ? buildLabelOptions(data) : buildCiteOptions(data),
      query,
    );
    if (options.length === 0) return null;
    return {
      from: context.pos - query.length,
      options,
      validFor: SYMBOL_KEY_VALID_FOR,
    };
  };
}

/**
 * LaTeX 行内补全扩展：命令 + 环境 + 引用三 source + 弹层主题
 * （completionTheme）。
 * viewer 只读态由各 source 内部短路（editable=false 本就不触发）。
 * @param {{onApplyEntry?: (entry: object) => void,
 *   getLanguage?: () => string,
 *   getRefSymbols?: () => Promise<object|null>}} [options]
 *   - onApplyEntry：应用补全后回调（EditorPage 接宏包缺失联动；决策 Q14
 *     与面板行为一致）；
 *   - getLanguage：当前界面语言读取器（缺省按未知语言处理 → 简体原样），
 *     每次补全触发时求值，语言切换无须重建扩展；
 *   - getRefSymbols：引用补全（\ref/\label/\cite）的项目符号数据读取器
 *     （阶段四；缺省时不挂引用 source）。
 * @returns {import("@codemirror/state").Extension}
 */
export function latexCompletionExtension({
  onApplyEntry,
  getLanguage = () => "",
  getRefSymbols,
} = {}) {
  const sources = [
    makeEnvironmentCompletionSource(onApplyEntry, getLanguage),
    // 两 source 均为异步（共享索引 / 中文名本地化），CM6 原生支持
    makeCommandCompletionSource(onApplyEntry, getLanguage),
  ];
  if (typeof getRefSymbols === "function") {
    sources.push(makeReferenceCompletionSource(getRefSymbols));
  }
  return [
    completionPopupTheme,
    autocompletion({
      override: sources,
      icons: false,
      closeOnBlur: true,
      defaultKeymap: true,
    }),
  ];
}
