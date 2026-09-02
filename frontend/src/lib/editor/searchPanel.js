/**
 * CM6 查找替换面板（editor-productivity-plan §5.3 阶段二）。
 *
 * basicSetup 已内置 searchKeymap（Ctrl/Cmd+F 唤起面板，openSearchPanel
 * 会自动挂载 search 扩展），本模块补齐两件事：
 *   1. 主题化：面板 / 输入框 / 按钮 / 匹配高亮全部改走全局主题 CSS 变量，
 *      亮暗主题零 JS 参与（与 LaTeXEditor 基础主题同一做法）；
 *   2. 本地化：@codemirror/search 的面板文案经 EditorState.phrases 呈现，
 *      这里把官方短语键（"Find" / "Replace" / "replaced $ matches"…）
 *      映射到 i18n 的 components:editor.searchPanel.* 命名空间，三语同步（zh/zh-TW/en）。
 *
 * 语言切换是低频事件：短语 facet 以 searchLanguageField（持当前语言码）为
 * 依赖，LaTeXEditor 在 i18n languageChanged 事件里 dispatch
 * setSearchLanguage 效果即可热更新当前视图；换档收起的存档 EditorState
 * 保留建档时的语言，换回后若语言已变，重开面板前经历整缓冲区重建时刷新
 * （可接受的边界，避免为短语单独重建撤销历史）。
 *
 * 纯函数 buildSearchPhrases / SEARCH_PHRASE_KEYS 可 node --test 直接覆盖，
 * 本模块不依赖应用级 i18n 单例（translate 由调用方注入）。
 */

import { StateEffect, StateField } from "@codemirror/state";
import { EditorState } from "@codemirror/state";
import { EditorView } from "@codemirror/view";
import { search } from "@codemirror/search";

/**
 * @codemirror/search 面板用到的官方短语键 → i18n 键映射。
 * `$` 为 CM6 的占位符（替换计数 / 行号），译文中必须保留。
 */
export const SEARCH_PHRASE_KEY_MAP = Object.freeze({
  Find: "components:editor.searchPanel.find",
  Replace: "components:editor.searchPanel.replace",
  next: "components:editor.searchPanel.next",
  previous: "components:editor.searchPanel.previous",
  all: "components:editor.searchPanel.all",
  "match case": "components:editor.searchPanel.matchCase",
  regexp: "components:editor.searchPanel.regexp",
  "by word": "components:editor.searchPanel.byWord",
  replace: "components:editor.searchPanel.replaceAction",
  "replace all": "components:editor.searchPanel.replaceAll",
  close: "components:editor.searchPanel.close",
  "current match": "components:editor.searchPanel.currentMatch",
  "on line": "components:editor.searchPanel.onLine",
  "replaced $ matches": "components:editor.searchPanel.replacedMatches",
  "replaced match on line $":
    "components:editor.searchPanel.replacedMatchOnLine",
  "Go to line": "components:editor.searchPanel.goToLine",
  go: "components:editor.searchPanel.go",
});

/**
 * 由翻译函数构建 CM6 短语表：只收录取到非空文案的键，其余键回落
 * CM6 官方英文（phrase() 的内建行为）。
 * @param {(key: string) => string} translate i18n 翻译函数（接收完整键）
 * @returns {Record<string, string>}
 */
export function buildSearchPhrases(translate) {
  const phrases = {};
  if (typeof translate !== "function") return phrases;
  for (const [phrase, key] of Object.entries(SEARCH_PHRASE_KEY_MAP)) {
    const text = translate(key);
    if (typeof text === "string" && text && text !== key) {
      phrases[phrase] = text;
    }
  }
  return phrases;
}

/** 面板 + 匹配高亮主题（全部走主题 CSS 变量，暗色自动适配） */
const searchPanelTheme = EditorView.theme({
  ".cm-panels": {
    backgroundColor: "var(--popover)",
    color: "var(--popover-foreground)",
    borderTop: "1px solid var(--border)",
    borderBottom: "1px solid var(--border)",
  },
  ".cm-panel.cm-search": {
    padding: "8px 10px",
    display: "flex",
    flexWrap: "wrap",
    alignItems: "center",
    gap: "2px",
    "& input, & button": {
      margin: "2px 4px 2px 0",
      font: "inherit",
      fontSize: "0.8125rem",
      color: "var(--foreground)",
    },
    "& input[type=checkbox]": {
      marginRight: "4px",
      accentColor: "var(--primary)",
    },
    "& input[type=text], & input:not([type])": {
      padding: "3px 6px",
      borderRadius: "6px",
      border: "1px solid var(--border)",
      backgroundColor: "var(--background)",
      outline: "none",
      "&:focus": { borderColor: "var(--ring)" },
    },
    "& button": {
      padding: "3px 8px",
      borderRadius: "6px",
      border: "1px solid var(--border)",
      backgroundColor: "var(--background)",
      cursor: "pointer",
      "&:hover": { backgroundColor: "var(--accent)" },
    },
    "& label": {
      margin: "2px 8px 2px 0",
      fontSize: "0.75rem",
      color: "var(--muted-foreground)",
      whiteSpace: "pre",
      display: "inline-flex",
      alignItems: "center",
      gap: "4px",
      cursor: "pointer",
    },
    "& [name=close]": {
      top: "6px",
      right: "6px",
      borderRadius: "6px",
      padding: "2px 6px",
    },
  },
  ".cm-searchMatch": {
    backgroundColor: "color-mix(in oklab, var(--primary) 22%, transparent)",
    outline: "1px solid color-mix(in oklab, var(--primary) 35%, transparent)",
  },
  ".cm-searchMatch-selected": {
    backgroundColor: "color-mix(in oklab, var(--primary) 40%, transparent)",
  },
});

/**
 * 查找替换面板扩展（主题 + 短语本地化；searchKeymap 由 basicSetup 提供）。
 * @param {{getLanguage?: () => string,
 *          translate?: (key: string) => string}} [options]
 *   - getLanguage：当前界面语言读取器（EditorState 建档时求值一次）；
 *   - translate：i18n 翻译函数（接收 components:editor.searchPanel.* 完整键，每次
 *     短语 facet 重算时调用）。
 * @returns {{extension: import("@codemirror/state").Extension,
 *            setSearchLanguage: StateEffect<string>}} 返回语言切换效果，
 *   供 LaTeXEditor 在 i18n languageChanged 时对活跃视图 dispatch 热更新。
 */
export function createSearchPanelExtension({
  getLanguage = () => "",
  translate = () => "",
} = {}) {
  const setSearchLanguage = StateEffect.define();
  const searchLanguageField = StateField.define({
    create: () => getLanguage(),
    update(value, tr) {
      for (const e of tr.effects) {
        if (e.is(setSearchLanguage)) return e.value;
      }
      return value;
    },
  });
  // 短语 facet 以语言字段为依赖：语言切换 effect 触发重算并取新译文
  const phrasesFacet = EditorState.phrases.compute(
    [searchLanguageField],
    (state) => buildSearchPhrases(translate),
  );
  return {
    extension: [
      searchPanelTheme,
      searchLanguageField,
      phrasesFacet,
      // 面板挂载点：openSearchPanel 虽会自动补挂，显式声明保证首次唤起
      // 即带主题与短语（免一次动态重配置）
      search(),
    ],
    setSearchLanguage,
  };
}
