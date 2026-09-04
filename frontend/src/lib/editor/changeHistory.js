/**
 * 修改行色条扩展（editor-productivity-plan 阶段八；借鉴 Notepad++ 8.4.6
 * Change History margin：橙条 = 有未保存修改的行，绿条 = 本会话改过且
 * 已保存的行）。
 *
 * 基线注入：setSavedTextEffect / setOpenedTextEffect 由 LaTeXEditor 随
 * tab 激活（换档/重建）与保存回写（markTabSaved）同步 dispatch，基线
 * 文本以 tab 记录的 savedBuffer / openedBuffer 为单一事实源。
 *
 * 渲染：Decoration.line 左侧内阴影条（零 gutter 列，不挤压代码区），
 * 橙色优先于绿色。
 *
 * 集合维护策略（性能取舍）：
 *   - 橙色集 = diff(saved, doc)，每次 doc 变更全量重算——基线与当前文
 *     档通常只差少量中段（公共前后缀剪除后 Myers 开销可忽略），且撤回
 *     到保存点时集合精确清零；
 *   - 绿色集采用「保存时冻结」模型：saved 基线效果触发（保存回写/换档
 *     同步/打开基线注入）时按 diff(opened, doc) 全量重建一次并存为冻结
 *     集；键入期间冻结集保持不动，展示集 = 冻结集 − 橙色集每步派生——
 *     已保存行被再编辑转橙、随后撤回时绿标可恢复。已知近似：键入期间
 *     冻结集的行号不随上方插入/删除漂移（仅越界行被过滤），精确值在下
 *     次保存 / 换档时重建；Notepad++ 原生 change history 在键入中同样以
 *     「行位置」近似持有标记。
 *
 * 纯逻辑 diff 在 lib/editor/changeHistoryDiff.js（独立单测），本模块
 * StateField 转移逻辑经 EditorState 可 node --test 直接覆盖。
 */

import { Decoration, EditorView, ViewPlugin } from "@codemirror/view";
import { StateEffect, StateField } from "@codemirror/state";
import { computeChangedLines } from "./changeHistoryDiff.js";

/** 注入保存基线（tab.savedBuffer；null = 无基线，降级为不显示色条） */
export const setSavedTextEffect = StateEffect.define();

/** 注入打开基线（tab.openedBuffer；null = 绿条从零重建） */
export const setOpenedTextEffect = StateEffect.define();

/** @returns {Set<number>} 未保存修改行（saved 无基线时恒空集） */
function unsavedLines(savedText, docText) {
  return savedText ? computeChangedLines(savedText, docText) : new Set();
}

/** @returns {Set<number>} from 中不在 without 的元素（新集合） */
function subtractSet(from, without) {
  if (without.size === 0) return from;
  const out = new Set();
  for (const value of from) {
    if (!without.has(value)) out.add(value);
  }
  return out;
}

/** @returns {Set<number>} 过滤越界行号（编辑删行后冻结集合的防御） */
function filterLinesWithin(set, maxLine) {
  let out = set;
  for (const line of set) {
    if (line > maxLine) {
      if (out === set) out = new Set(set);
      out.delete(line);
    }
  }
  return out;
}

export const changeHistoryField = StateField.define({
  create() {
    return {
      savedText: null,
      openedText: null,
      unsaved: new Set(),
      savedFrozen: new Set(),
      saved: new Set(),
    };
  },
  update(value, tr) {
    let savedText = value.savedText;
    let openedText = value.openedText;
    let baselineChanged = false;
    for (const effect of tr.effects) {
      if (effect.is(setSavedTextEffect)) {
        if (effect.value !== savedText) {
          savedText = effect.value;
          baselineChanged = true;
        }
      } else if (effect.is(setOpenedTextEffect)) {
        if (effect.value !== openedText) {
          openedText = effect.value;
          baselineChanged = true;
        }
      }
    }
    if (!tr.docChanged && !baselineChanged) return value;
    const docText = tr.state.doc.toString();
    const unsaved = unsavedLines(savedText, docText);
    let savedFrozen;
    if (baselineChanged) {
      // 保存回写 / 基线换档：冻结绿集按打开基线全量重建
      savedFrozen = openedText
        ? subtractSet(computeChangedLines(openedText, docText), unsaved)
        : new Set();
    } else {
      // 键入：冻结集保持不动（只过滤越界行号），派生集每步重算——
      // 已保存行被再编辑转橙后，撤回时能恢复绿标（性能取舍见模块头注释）
      savedFrozen = filterLinesWithin(value.savedFrozen, tr.state.doc.lines);
    }
    return {
      savedText,
      openedText,
      unsaved,
      savedFrozen,
      saved: subtractSet(savedFrozen, unsaved),
    };
  },
});

/**
 * 读取当前基线（LaTeXEditor 基线同步 effect 用于跳过冗余 dispatch）。
 * @returns {{savedText: string|null, openedText: string|null}|null}
 */
export function getChangeBaselines(state) {
  const field = state.field(changeHistoryField, false);
  if (!field) return null;
  return { savedText: field.savedText, openedText: field.openedText };
}

/** @returns {RangeSet<Decoration>} 橙色优先的行装饰集 */
function buildChangeDecorations(state) {
  const field = state.field(changeHistoryField, false);
  const ranges = [];
  if (field) {
    const push = (lines, cls) => {
      for (const line of lines) {
        if (line > state.doc.lines) continue;
        ranges.push(
          Decoration.line({ class: cls }).range(state.doc.line(line).from),
        );
      }
    };
    push(field.unsaved, "cm-changebar-unsaved");
    push(field.saved, "cm-changebar-saved");
  }
  return Decoration.set(ranges, true);
}

const changeHistoryPlugin = ViewPlugin.fromClass(
  class {
    constructor(view) {
      this.decorations = buildChangeDecorations(view.state);
    }
    update(update) {
      if (
        update.docChanged ||
        update.state.field(changeHistoryField) !==
          update.startState.field(changeHistoryField)
      ) {
        this.decorations = buildChangeDecorations(update.state);
      }
    }
  },
  { decorations: (plugin) => plugin.decorations },
);

/** 色条配色：琥珀（未保存）/ 翠绿（已保存），亮暗主题均可读 */
const changeHistoryTheme = EditorView.theme({
  ".cm-line.cm-changebar-unsaved": {
    boxShadow: "inset 3px 0 0 0 #f59e0b",
  },
  ".cm-line.cm-changebar-saved": {
    boxShadow: "inset 3px 0 0 0 #10b981",
  },
});

/** @returns {Extension[]} 修改行色条扩展组（字段稳定，重配置零副作用） */
export function changeHistoryExtension() {
  return [changeHistoryField, changeHistoryPlugin, changeHistoryTheme];
}
