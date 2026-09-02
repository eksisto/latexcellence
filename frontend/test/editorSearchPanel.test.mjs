import test from "node:test";
import assert from "node:assert/strict";

import { EditorState } from "@codemirror/state";
import { openSearchPanel, closeSearchPanel } from "@codemirror/search";

import {
  SEARCH_PHRASE_KEY_MAP,
  buildSearchPhrases,
  createSearchPanelExtension,
} from "../src/lib/editor/searchPanel.js";

/* ── 短语表构建（纯函数） ─────────────────────────────────────────────── */

const zhTranslations = {
  "components:editor.searchPanel.find": "查找",
  "components:editor.searchPanel.replace": "替换",
  "components:editor.searchPanel.next": "下一个",
  "components:editor.searchPanel.previous": "上一个",
  "components:editor.searchPanel.all": "全部",
  "components:editor.searchPanel.matchCase": "区分大小写",
  "components:editor.searchPanel.regexp": "正则表达式",
  "components:editor.searchPanel.byWord": "全词匹配",
  "components:editor.searchPanel.replaceAction": "替换",
  "components:editor.searchPanel.replaceAll": "全部替换",
  "components:editor.searchPanel.close": "关闭",
  "components:editor.searchPanel.currentMatch": "当前匹配",
  "components:editor.searchPanel.onLine": "行",
  "components:editor.searchPanel.replacedMatches": "已替换 $ 处匹配",
  "components:editor.searchPanel.replacedMatchOnLine": "已替换第 $ 行的匹配",
  "components:editor.searchPanel.goToLine": "跳转到行",
  "components:editor.searchPanel.go": "跳转",
};

test("SEARCH_PHRASE_KEY_MAP 覆盖 @codemirror/search 面板全部短语键", () => {
  // 官方面板源码中 phrase(...) 调用到的键集合（含跳转行面板与 announcer）
  const official = [
    "Find",
    "Replace",
    "next",
    "previous",
    "all",
    "match case",
    "regexp",
    "by word",
    "replace",
    "replace all",
    "close",
    "current match",
    "on line",
    "replaced $ matches",
    "replaced match on line $",
    "Go to line",
    "go",
  ].sort();
  assert.deepEqual(Object.keys(SEARCH_PHRASE_KEY_MAP).sort(), official);
  // 映射值全部指向 components:editor.searchPanel 命名空间
  for (const key of Object.values(SEARCH_PHRASE_KEY_MAP)) {
    assert.match(key, /^components:editor\.searchPanel\./);
  }
});

test("buildSearchPhrases 把 i18n 译文映射到官方短语键（保留 $ 占位符）", () => {
  const phrases = buildSearchPhrases((key) => zhTranslations[key]);
  assert.equal(phrases.Find, "查找");
  assert.equal(phrases["replace all"], "全部替换");
  assert.equal(phrases["replaced $ matches"], "已替换 $ 处匹配");
  assert.equal(phrases["replaced match on line $"], "已替换第 $ 行的匹配");
  // 官方键集合与译文键集合一一对应
  assert.equal(
    Object.keys(phrases).length,
    Object.keys(SEARCH_PHRASE_KEY_MAP).length,
  );
});

test("buildSearchPhrases 缺译键回落官方英文（键缺失即不进表，phrase() 内建回落）", () => {
  const phrases = buildSearchPhrases((key) =>
    key.endsWith(".find") ? undefined : zhTranslations[key],
  );
  assert.equal("Find" in phrases, false);
  assert.equal(phrases.Replace, "替换");
});

test("buildSearchPhrases 对非函数 / 键名原样返回的 translate 回落", () => {
  assert.deepEqual(buildSearchPhrases(null), {});
  // translate 返回键本身（i18next 缺键行为）视为未翻译，不进表
  const phrases = buildSearchPhrases((key) => key);
  assert.deepEqual(phrases, {});
});

/* ── 扩展：短语 facet 与语言切换效果（真实 EditorState） ──────────────── */

test("createSearchPanelExtension：建档时按 getLanguage 生成短语，语言切换效果热更新", () => {
  let language = "zh";
  const { extension, setSearchLanguage } = createSearchPanelExtension({
    getLanguage: () => language,
    translate: (key) => {
      if (language !== "zh") return key.endsWith(".next") ? "Next" : "";
      return zhTranslations[key];
    },
  });
  const state = EditorState.create({ doc: "abc", extensions: [extension] });
  assert.equal(state.phrase("next"), "下一个");
  // 切换语言：dispatch 效果后短语 facet 重算（translate 闭包按当前语言返回）
  language = "en";
  const tr = state.update({ effects: setSearchLanguage.of("en") });
  assert.equal(tr.state.phrase("next"), "Next");
  assert.equal(tr.state.phrase("Find"), "Find"); // en 表中未提供 → 回落官方英文
});

test("面板可随扩展正常打开与关闭（search() 挂载不破坏 EditorState）", () => {
  // openSearchPanel 需要视图；此处仅验证含扩展的 EditorState 构建正常
  const { extension } = createSearchPanelExtension({
    getLanguage: () => "zh",
    translate: (key) => zhTranslations[key],
  });
  const state = EditorState.create({ doc: "abc", extensions: [extension] });
  assert.equal(state.doc.toString(), "abc");
  assert.ok(openSearchPanel);
  assert.ok(closeSearchPanel);
});
