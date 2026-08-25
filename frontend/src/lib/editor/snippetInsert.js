/**
 * 模板插入与占位符跳转（editor-plan §6.3 / §10 键位表 / 决策 Q9）。
 *
 * 两部分：
 *   1. 纯函数层（node --test 直接覆盖）：
 *      - parsePlaceholders / renderSnippet：解析 `$1/$2…` 占位符与 `$0`
 *        退出位置，产出剥离占位符后的文本与各停泊点区间；
 *      - 网格骨架生成：%COLS% → 列格式串，%CELLS% → m 行 n 列单元格，
 *        占位符编号接续模板中已有编号，落点在各单元格。
 *   2. CM6 层：插入后进入占位符跳转模式——StateField 持有停泊点区间并随
 *      文档变更自动映射；Tab 在 `$1…$n` 间循环、`$0` 为最后一站后退出，
 *      Shift+Tab 反向，Esc 随时退出。行内补全与命令面板共用同一入口。
 */

import { StateEffect, StateField, Prec } from "@codemirror/state";
import { Decoration, EditorView, WidgetType, keymap } from "@codemirror/view";

/* ── 占位符解析（纯函数） ─────────────────────────────────────────────── */

const PLACEHOLDER_RE = /\$(\d+)/g;

/**
 * 解析模板中的占位符（$1…$n 与 $0），按编号升序返回其在原文中的位置。
 * 后跟非数字的 `$` 视为字面字符保留。
 * @param {string} template 插入模板
 * @returns {{num: number, start: number, end: number}[]}
 */
export function parsePlaceholders(template) {
  if (typeof template !== "string") return [];
  const found = [];
  PLACEHOLDER_RE.lastIndex = 0;
  let m;
  while ((m = PLACEHOLDER_RE.exec(template)) !== null) {
    found.push({
      num: Number(m[1]),
      start: m.index,
      end: m.index + m[0].length,
    });
  }
  return found.sort((a, b) => a.num - b.num);
}

/** 模板中已使用的最大占位符编号（网格单元格从 max+1 接续编号） */
export function nextPlaceholderNumber(template) {
  const nums = parsePlaceholders(template).map((p) => p.num);
  return nums.length > 0 ? Math.max(...nums) + 1 : 1;
}

/**
 * 将模板渲染为可插入文本：剥离全部 `$n` 标记，同时给出每个占位符在
 * 结果文本中的区间与退出位置（$0；无则 null）。
 * @param {string} template
 * @returns {{text: string, ranges: {from:number,to:number,num:number}[], exit: number|null}}
 */
export function renderSnippet(template) {
  if (typeof template !== "string") {
    return { text: "", ranges: [], exit: null };
  }
  const marks = [...template.matchAll(PLACEHOLDER_RE)];
  let text = "";
  let cursor = 0;
  const ranges = [];
  let exit = null;
  for (const m of marks) {
    text += template.slice(cursor, m.index);
    const from = text.length;
    const to = from; // 占位符初始为空选区
    const num = Number(m[1]);
    if (num === 0) {
      exit = from;
    } else {
      ranges.push({ from, to, num });
    }
    cursor = m.index + m[0].length;
  }
  text += template.slice(cursor);
  // 停泊顺序按编号升序（$1 → $n）
  ranges.sort((a, b) => a.num - b.num);
  return { text, ranges, exit };
}

/* ── 网格骨架生成（纯函数，决策 Q9） ──────────────────────────────────── */

/** 网格模板中列格式串的替换标记 */
export const GRID_COL_MARKER = "%COLS%";
/** 网格模板中单元格骨架的替换标记 */
export const GRID_CELL_MARKER = "%CELLS%";
/** 单元格间分隔符（同行列内） */
const CELL_SEP = " & ";
/** 行分隔符（LaTeX 换行） */
const ROW_SEP = " \\\\\n\t";

/**
 * 是否为网格参数化模板（含 %CELLS% 标记）。
 * @param {string} template
 */
export function isGridTemplate(template) {
  return typeof template === "string" && template.includes(GRID_CELL_MARKER);
}

/** 列格式串：统一 c 列居中 */
export function buildGridColSpec(cols) {
  return "c".repeat(Math.max(1, cols));
}

/**
 * 生成 m 行 n 列单元格骨架：占位符按行优先顺序连续编号，
 * 行内单元格以 ` & ` 分隔，行之间以 ` \\ ` + 换行 + 缩进分隔。
 * @param {number} rows 行数
 * @param {number} cols 列数
 * @param {number} [startNum=1] 起始占位符编号（接续模板已有编号）
 * @returns {string}
 */
export function buildGridCells(rows, cols, startNum = 1) {
  const r = Math.min(Math.max(1, rows), 99);
  const c = Math.min(Math.max(1, cols), 99);
  const lines = [];
  let n = Math.max(1, startNum);
  for (let i = 0; i < r; i += 1) {
    const cells = [];
    for (let j = 0; j < c; j += 1) {
      cells.push(`$${n}`);
      n += 1;
    }
    lines.push(cells.join(CELL_SEP));
  }
  return lines.join(ROW_SEP);
}

/**
 * 展开网格模板：%COLS% 替换为列格式串，%CELLS% 替换为 m×n 骨架
 * （单元格占位符编号接续模板中已有占位符，如三线表的表题 $1）。
 * 返回结果仍是含 `$n` 的模板字符串，交由 renderSnippet 二次渲染。
 * @param {string} template 含标记的原始 insert
 * @param {number} rows 选定行数
 * @param {number} cols 选定列数
 */
export function renderGridTemplate(template, rows, cols) {
  if (!isGridTemplate(template)) return template;
  return template
    .replace(GRID_COL_MARKER, buildGridColSpec(cols))
    .replace(
      GRID_CELL_MARKER,
      buildGridCells(rows, cols, nextPlaceholderNumber(template)),
    );
}

/**
 * 条目 → 最终插入模板的统一出口：带 grid 的条目用选定行列展开骨架，
 * 其余条目原样返回 insert。
 * @param {{insert:string, grid:{rows:number,cols:number}|null}} entry
 * @param {{rows:number,cols:number}} [size] 网格选择器选定的行列
 */
export function expandEntryInsert(entry, size) {
  if (!entry || typeof entry.insert !== "string") return "";
  if (entry.grid && isGridTemplate(entry.insert)) {
    const rows = size?.rows ?? entry.grid.rows;
    const cols = size?.cols ?? entry.grid.cols;
    return renderGridTemplate(entry.insert, rows, cols);
  }
  return entry.insert;
}

/* ── CM6 占位符跳转模式 ───────────────────────────────────────────────── */

const setPlaceholders = StateEffect.define();
const clearPlaceholders = StateEffect.define();

/**
 * 占位符状态字段：{ranges, index, exit} | null。
 * index 指当前停泊的 ranges 下标；exit 为 $0 位置（可能不在 ranges 内）。
 */
const placeholderField = StateField.define({
  // 注意：CM6 StateField.define 的初始值选项名是 create（非 init），
  // 写错会被静默忽略，导致含本扩展的 EditorState 构建即抛错
  create: () => null,
  update(value, tr) {
    for (const e of tr.effects) {
      if (e.is(setPlaceholders)) return e.value;
      if (e.is(clearPlaceholders)) return null;
    }
    if (!value) return null;
    // 与 CM6 history 的互操作：撤销事务直接退出占位符模式。
    // 否则插入模板被整体撤销后，停泊点会残留为空区间集合，
    // 持续劫持 Tab/Shift+Tab/Esc 并留下悬空高亮。
    if (tr.isUserEvent("undo")) return null;
    if (tr.docChanged) {
      const ranges = [];
      for (const r of value.ranges) {
        // from 向左结合、to 向右结合：在占位符内输入时区间随之扩张
        const from = tr.changes.mapPos(r.from);
        const to = tr.changes.mapPos(r.to, 1);
        if (to >= from) ranges.push({ from, to });
      }
      if (ranges.length === 0) return null;
      return {
        ranges,
        exit: value.exit != null ? tr.changes.mapPos(value.exit) : null,
        index: Math.min(value.index, ranges.length - 1),
      };
    }
    return value;
  },
});

const activeMark = Decoration.mark({ class: "cm-snippet-placeholder-active" });
const pendingMark = Decoration.mark({
  class: "cm-snippet-placeholder-pending",
});

/**
 * 空占位符的点状标记：CM6 的 mark decoration 不允许零宽区间
 * （range(from>=to) 直接抛 "Mark decorations may not be empty"），
 * 初始为空的停泊点必须降级为 widget 点装饰；非空后仍用 mark 包裹内容。
 */
class PlaceholderDot extends WidgetType {
  constructor(active) {
    super();
    this.active = active;
  }
  eq(other) {
    return other.active === this.active;
  }
  toDOM() {
    const span = document.createElement("span");
    span.className = this.active
      ? "cm-snippet-placeholder-dot is-active"
      : "cm-snippet-placeholder-dot";
    return span;
  }
  ignoreEvent() {
    return false;
  }
}
const emptyActiveDot = Decoration.widget({ widget: new PlaceholderDot(true) });
const emptyPendingDot = Decoration.widget({
  widget: new PlaceholderDot(false),
});

const placeholderDecorations = EditorView.decorations.compute(
  [placeholderField],
  (state) => {
    const value = state.field(placeholderField);
    if (!value) return Decoration.none;
    const builder = Decoration.set(
      value.ranges.map((r, i) =>
        r.from === r.to
          ? (i === value.index ? emptyActiveDot : emptyPendingDot).range(r.from)
          : (i === value.index ? activeMark : pendingMark).range(r.from, r.to),
      ),
      true,
    );
    return builder;
  },
);

/** 当前是否处于占位符跳转模式 */
export function placeholderActive(state) {
  return state.field(placeholderField, false) !== null;
}

/**
 * 当前占位符停泊区间快照（随编辑映射后的新文档坐标；未激活返回 null）。
 * 供测试与外部诊断观察 StateField 的区间映射行为。
 * @returns {{from:number,to:number}[]|null}
 */
export function placeholderRanges(state) {
  const value = state.field(placeholderField, false);
  return value ? value.ranges.map((r) => ({ from: r.from, to: r.to })) : null;
}

function jumpTo(view, target) {
  view.dispatch({
    selection: { anchor: target.from, head: target.to },
    scrollIntoView: true,
  });
}

/** Tab：在占位符间前进；有 $0 时其为止点后退出，无 $0 则回绕循环（Esc 退出） */
export function nextPlaceholder(view) {
  const value = view.state.field(placeholderField, false);
  if (!value) return false;
  const totalStops = value.ranges.length + (value.exit != null ? 1 : 0);
  const nextIndex = value.index + 1;
  if (nextIndex >= totalStops) {
    if (value.exit != null) {
      return exitPlaceholderMode(view);
    }
    // 无 $0 的模板：回到首站继续循环
    view.dispatch({
      effects: setPlaceholders.of({ ...value, index: 0 }),
    });
    jumpTo(view, value.ranges[0]);
    return true;
  }
  if (nextIndex < value.ranges.length) {
    view.dispatch({
      effects: setPlaceholders.of({ ...value, index: nextIndex }),
    });
    jumpTo(view, value.ranges[nextIndex]);
  } else {
    // 进入 $0 退出位置：光标落到退出点并结束模式（单事务完成）
    const exit = value.exit ?? view.state.selection.main.head;
    view.dispatch({
      effects: clearPlaceholders.of(null),
      selection: { anchor: exit, head: exit },
      scrollIntoView: true,
    });
  }
  return true;
}

/** Shift+Tab：反向跳转（首站再向前则回绕到末站） */
export function previousPlaceholder(view) {
  const value = view.state.field(placeholderField, false);
  if (!value) return false;
  const prevIndex =
    value.index <= 0 ? value.ranges.length - 1 : value.index - 1;
  view.dispatch({
    effects: setPlaceholders.of({ ...value, index: prevIndex }),
  });
  jumpTo(view, value.ranges[prevIndex]);
  return true;
}

/** Esc / 完成退出：清除占位符状态 */
export function exitPlaceholderMode(view) {
  if (!view.state.field(placeholderField, false)) return false;
  view.dispatch({ effects: clearPlaceholders.of(null) });
  return true;
}

/**
 * 插入模板并进入占位符跳转模式（双入口共用）。
 * @param {EditorView} view CM6 视图
 * @param {string} template 含 $n 占位符的模板（网格条目应先经 expandEntryInsert）
 * @param {{from?: number, to?: number}} [range] 替换区间；默认主选区
 */
export function insertSnippet(view, template, range) {
  if (!view || typeof template !== "string" || !template) return false;
  const { text, ranges, exit } = renderSnippet(template);
  const main = view.state.selection.main;
  const from = Math.min(range?.from ?? main.from, range?.to ?? main.to);
  const to = Math.max(range?.from ?? main.from, range?.to ?? main.to);

  if (ranges.length === 0) {
    // 无 $n 占位符：纯文本（或仅 $0）直接插入，光标落到退出点
    insertExit(view, { text, exit }, from, to);
    return finishInsert(view);
  }

  const base = from;
  const newRanges = ranges.map((r) => ({
    from: base + r.from,
    to: base + r.to,
  }));
  const newExit = exit != null ? base + exit : null;

  view.dispatch({
    changes: { from, to, insert: text },
    effects: setPlaceholders.of({
      ranges: newRanges,
      exit: newExit,
      index: 0,
    }),
    selection: { anchor: newRanges[0].from, head: newRanges[0].to },
    scrollIntoView: true,
  });
  view.focus();
  return true;
}

/** 纯文本插入的落点：$0 位置优先，否则插入文本末尾 */
function insertExit(view, rendered, from, to) {
  const cursor =
    rendered.exit != null ? from + rendered.exit : from + rendered.text.length;
  view.dispatch({
    changes: { from, to, insert: rendered.text },
    selection: { anchor: cursor, head: cursor },
    scrollIntoView: true,
  });
  return cursor;
}

function finishInsert(view) {
  view.focus();
  return true;
}

/** 占位符高亮主题 + 字段 + 键映射（挂进编辑器扩展数组即可） */
const placeholderTheme = EditorView.theme({
  ".cm-snippet-placeholder-active": {
    backgroundColor: "color-mix(in oklab, var(--primary) 30%, transparent)",
    outline: "1px solid color-mix(in oklab, var(--primary) 60%, transparent)",
  },
  ".cm-snippet-placeholder-pending": {
    backgroundColor: "color-mix(in oklab, var(--muted) 80%, transparent)",
  },
  // 空占位符的可见点标记（widget 装饰）
  ".cm-snippet-placeholder-dot": {
    display: "inline-block",
    width: "2px",
    height: "1em",
    minHeight: "0.8em",
    verticalAlign: "text-bottom",
    borderRadius: "1px",
    backgroundColor:
      "color-mix(in oklab, var(--muted-foreground) 45%, transparent)",
  },
  ".cm-snippet-placeholder-dot.is-active": {
    width: "3px",
    backgroundColor: "color-mix(in oklab, var(--primary) 75%, transparent)",
  },
});

export function snippetPlaceholderExtension() {
  return [
    placeholderField,
    placeholderDecorations,
    placeholderTheme,
    // 高优先级：占位符模式下 Tab/Esc 必须先于默认键位生效
    Prec.high(
      keymap.of([
        { key: "Tab", run: nextPlaceholder },
        { key: "Shift-Tab", run: previousPlaceholder },
        { key: "Escape", run: exitPlaceholderMode },
      ]),
    ),
  ];
}
