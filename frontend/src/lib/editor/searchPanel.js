/**
 * CM6 查找替换面板（editor-productivity-design §5.3 阶段二）。
 *
 * basicSetup 已内置 searchKeymap（Ctrl/Cmd+F 唤起面板，openSearchPanel
 * 会自动挂载 search 扩展），本模块补齐两件事：
 *   1. 主题化：面板 / 输入框 / 按钮 / 匹配高亮全部改走全局主题 CSS 变量，
 *      亮暗主题零 JS 参与（与 LaTeXEditor 基础主题同一做法）。注意按钮
 *      必须显式 appearance:none——Tailwind preflight 给 button 强制了
 *      appearance:button，原生渐变背景图会盖在主题纯色之上；
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
    // 保持 block 流：CM 在替换行前插入的 <br> 只在块级布局中生效，
    // flex/grid 下它是零尺寸 item 且盒尺寸不可被 CSS 改写（折行失效）
    "& input, & button": {
      margin: "2px 4px 2px 0",
      font: "inherit",
      fontSize: "0.8125rem",
      color: "var(--foreground)",
      verticalAlign: "middle",
    },
    // 勾选框：appearance:none 纯 CSS 自绘（对齐全站 shadcn Checkbox 观感）。
    // 不能依赖原生绘制 + accent-color：原生控件的绘制不保证跟随过渡中间值
    // 逐帧重绘，亮/暗切换时计算值在渐变而外观瞬间跳变。自绘后颜色落在
    // 普通的 background-color / border-color 上，随全局主题过渡可靠渐变；
    // 勾选态附加 150ms scale 微动画（View Transitions 路径下两者均被
    // :active-view-transition 关闭，由整页交叉淡入接管，不会叠加）
    "& input[type=checkbox]": {
      appearance: "none",
      width: "0.875rem",
      height: "0.875rem",
      borderRadius: "4px",
      border: "1px solid var(--border)",
      backgroundColor: "var(--background)",
      display: "inline-grid",
      placeContent: "center",
      cursor: "pointer",
      transition:
        "background-color 300ms cubic-bezier(0.4, 0, 0.2, 1), border-color 300ms cubic-bezier(0.4, 0, 0.2, 1)",
      "&::before": {
        content: '""',
        width: "0.5rem",
        height: "0.5rem",
        backgroundColor: "var(--primary-foreground)",
        clipPath:
          "polygon(14% 44%, 0 65%, 50% 100%, 100% 16%, 80% 0%, 43% 62%)",
        transform: "scale(0)",
        transition: "transform 150ms cubic-bezier(0.4, 0, 0.2, 1)",
      },
      "&:checked": {
        borderColor: "var(--primary)",
        backgroundColor: "var(--primary)",
        "&::before": { transform: "scale(1)" },
      },
      "&:focus-visible": {
        outline: "2px solid color-mix(in oklch, var(--ring) 70%, transparent)",
        outlineOffset: "2px",
      },
    },
    "& input[type=text], & input:not([type])": {
      padding: "3px 6px",
      borderRadius: "6px",
      border: "1px solid var(--border)",
      backgroundColor: "var(--background)",
      outline: "none",
      "&:focus": {
        borderColor: "var(--ring)",
        boxShadow:
          "0 0 0 3px color-mix(in oklch, var(--ring) 50%, transparent)",
      },
    },
    // 按钮走主题纯色（对齐全站 Button secondary 变体）。appearance:none
    // 与 background-image:none 必须显式声明：Tailwind preflight 给 button
    // 强制了 appearance:button，UA 样式表还为 button 声明了原生渐变
    // background-image——仅设主题 background-color 压不住它（拟物质感 +
    // 暗色模式下亮灰底白字不可读的根源）
    "& button": {
      appearance: "none",
      backgroundImage: "none",
      padding: "3px 8px",
      borderRadius: "6px",
      border: "1px solid var(--border)",
      backgroundColor: "var(--secondary)",
      color: "var(--secondary-foreground)",
      cursor: "pointer",
      "&:hover": {
        backgroundColor:
          "color-mix(in oklch, var(--secondary), var(--foreground) 5%)",
      },
      "&:focus-visible": {
        borderColor: "var(--ring)",
        boxShadow:
          "0 0 0 3px color-mix(in oklch, var(--ring) 50%, transparent)",
      },
    },
    "& label": {
      margin: "2px 8px 2px 0",
      fontSize: "0.75rem",
      color: "var(--muted-foreground)",
      whiteSpace: "pre",
      display: "inline-flex",
      alignItems: "center",
      verticalAlign: "middle",
      gap: "4px",
      cursor: "pointer",
    },
    // 关闭按钮：ghost 样式（对齐全站 Button ghost 变体）；hover 用半透明
    // muted 混色，亮暗主题各自成立
    "& [name=close]": {
      appearance: "none",
      backgroundImage: "none",
      backgroundColor: "transparent",
      borderColor: "transparent",
      color: "var(--muted-foreground)",
      top: "6px",
      right: "6px",
      borderRadius: "6px",
      padding: "2px 6px",
      cursor: "pointer",
      "&:hover": {
        backgroundColor: "color-mix(in oklch, var(--muted) 60%, transparent)",
        color: "var(--foreground)",
      },
      "&:focus-visible": {
        borderColor: "var(--ring)",
        boxShadow:
          "0 0 0 3px color-mix(in oklch, var(--ring) 50%, transparent)",
      },
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
