/**
 * 包裹命令（editor-productivity-design §5.3 阶段二 / 决策 Q13）。
 *
 * 三层：
 *   1. 纯函数层（node --test 直接覆盖）：computeWrapEdits 对若干选区计算
 *      包裹编辑与新选区落点——有内容选区包成 `\cmd{内容}` 并保持内容选中
 *      （便于连续叠加包裹：粗体 → 斜体嵌套）；空选区（光标）插入空命令
 *      `\cmd{}` 并把光标定位到参数内；
 *   2. CM6 命令层：wrapViewSelections 把纯函数结果 dispatch 到视图
 *      （多选区逐个包裹，单事务完成，Ctrl+Z 一步撤销）；
 *   3. 键映射层：Ctrl/Cmd+B / I / U / K 直包 \textbf / \emph /
 *      \underline / \texttt；Ctrl+L 浮层菜单（命令面板 wrap 过滤视图）
 *      覆盖 wrap 全集，动作同走本模块。
 *
 * wrap 类目条目来自 data/latexCommands/wrap.json；命令面板对
 * category === "wrap" 的条目执行包裹动作而非占位符插入（EditorPage 分派）。
 */

import { EditorSelection, Prec } from "@codemirror/state";
import { keymap } from "@codemirror/view";

/** wrap 类目标识（与 data/latexCommands/wrap.json 的 category 一致） */
export const WRAP_CATEGORY = "wrap";

/** LaTeX 命令名约束（与命令数据库 name 校验同规则） */
const COMMAND_NAME_RE = /^[A-Za-z][A-Za-z0-9*]*$/;

/** 直包快捷键（决策 Q13；Mod = macOS Cmd / 其余 Ctrl） */
export const WRAP_DIRECT_SHORTCUTS = Object.freeze([
  Object.freeze({ key: "Mod-b", command: "textbf" }),
  Object.freeze({ key: "Mod-i", command: "emph" }),
  Object.freeze({ key: "Mod-u", command: "underline" }),
  Object.freeze({ key: "Mod-k", command: "texttt" }),
]);

/**
 * 对若干选区计算包裹编辑与新选区落点。
 *
 * @param {string} docText 缓冲区全文
 * @param {{from?: number, to?: number, anchor?: number, head?: number}[]}
 *   ranges 选区区间（按 from 升序、互不重叠；CM6 EditorSelection 保证，
 *   本函数仍做防御性校验；SelectionRange 的 anchor/head 同时兼容，用于
 *   保留选区方向）
 * @param {string} command 不含反斜杠的命令名（如 "textbf"）
 * @returns {{changes: {from: number, to: number, insert: string}[],
 *            selections: {anchor: number, head: number}[]}|null}
 *   无可执行编辑（命令名非法 / 区间非法 / 重叠）时返回 null，调用方保持
 *   默认行为（返回 false 交还给后续键位处理）。
 */
export function computeWrapEdits(docText, ranges, command) {
  if (typeof docText !== "string" || !COMMAND_NAME_RE.test(command ?? "")) {
    return null;
  }
  if (!Array.isArray(ranges) || ranges.length === 0) return null;
  const max = docText.length;
  const clamp = (v) => Math.max(0, Math.min(v ?? 0, max));
  const sorted = ranges
    .map((r) => {
      const lo = clamp(Math.min(r?.from ?? r?.anchor, r?.to ?? r?.head));
      const hi = clamp(Math.max(r?.from ?? r?.anchor, r?.to ?? r?.head));
      const anchor = clamp(r?.anchor ?? lo);
      const head = clamp(r?.head ?? hi);
      return { from: lo, to: hi, forward: head >= anchor };
    })
    .sort((a, b) => a.from - b.from || a.to - b.to);
  for (let i = 1; i < sorted.length; i += 1) {
    // 重叠选区包裹语义不明确，整体拒绝（CM6 不会产生此类选区）
    if (sorted[i].from < sorted[i - 1].to) return null;
  }

  const prefix = `\\${command}{`;
  const changes = [];
  const selections = [];
  let shift = 0; // 前序包裹引起的坐标偏移累计
  for (const range of sorted) {
    const content = docText.slice(range.from, range.to);
    const insert = `${prefix}${content}}`;
    changes.push({ from: range.from, to: range.to, insert });
    const contentFrom = range.from + shift + prefix.length;
    const contentTo = contentFrom + content.length;
    // 保持方向：正向选区保持正向（内容整体保持选中，便于连续叠加包裹：
    // 粗体 → 斜体嵌套）；反向选区方向亦保留
    selections.push(
      range.forward
        ? { anchor: contentFrom, head: contentTo }
        : { anchor: contentTo, head: contentFrom },
    );
    shift += insert.length - (range.to - range.from);
  }
  return { changes, selections };
}

/**
 * CM6 命令：对视图当前全部选区执行包裹（多光标逐个包裹，单事务完成）。
 * @param {import("@codemirror/view").EditorView} view
 * @param {string} command 命令名（不含反斜杠）
 * @returns {boolean} 是否执行了包裹（false = 只读 / 无视图 / 非法命令，
 *   键映射语义下交还给后续处理）
 */
export function wrapViewSelections(view, command) {
  if (!view || view.destroyed || view.state.readOnly) return false;
  const result = computeWrapEdits(
    view.state.doc.toString(),
    view.state.selection.ranges,
    command,
  );
  if (!result) return false;
  view.dispatch({
    changes: result.changes,
    selection: EditorSelection.create(
      result.selections.map((s) => EditorSelection.range(s.anchor, s.head)),
    ),
    scrollIntoView: true,
  });
  view.focus();
  return true;
}

/**
 * 直包快捷键映射（高优先级，避免与未来 basicSetup 键位冲突）。
 * @returns {import("@codemirror/state").Extension}
 */
export function wrapKeymapExtension() {
  return Prec.high(
    keymap.of(
      WRAP_DIRECT_SHORTCUTS.map(({ key, command }) => ({
        key,
        run: (view) => wrapViewSelections(view, command),
      })),
    ),
  );
}
