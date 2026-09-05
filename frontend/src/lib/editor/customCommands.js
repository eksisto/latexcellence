/**
 * 自定义片段消费侧合并层（editor v3 阶段二 / 决策 Q8）。
 *
 * data/latexCommands JSON 保持构建期冻结；用户自定义片段（存于编辑器偏好
 * persist 的 customSnippets 数组，清洗规则见 preferences.js）在运行期经本
 * 模块合并进命令数据库的各消费入口：
 *   - 行内补全 source（latexCompletion.js，命令 + 环境两路）；
 *   - 命令面板 / 包裹浮层数据（CommandPalette.jsx，独立 "custom" 分组）；
 *   - 插入入口（EditorPage → snippetInsert，机制层零改动复用）。
 *
 * 存储 schema（preferences.sanitizeCustomSnippet 白名单清洗）：
 *   { name, insert, zh, category: "custom" }
 * 运行时条目（normalizeCustomSnippet）补齐内建 schema 的 pkg / grid 字段：
 * insert 含 %CELLS% 网格标记时派生默认网格行列——%COLS%/%CELLS% 对自定义
 * 条目天然生效（expandEntryInsert / GridSizePicker 原样复用，机制层零改动）。
 *
 * 合并语义：内建条目在前、自定义追加在后（补全默认列表与面板分组中自定义
 * 置尾）；与内建同名的自定义条目在合并时丢弃（存储层保留，避免运行期命中
 * 歧义）。纯函数，供 node --test 直接单测。
 */

import {
  LATEX_COMMANDS,
  groupCommandsByCategory,
} from "../../data/latexCommands/index.js";
import { isGridTemplate } from "./snippetInsert.js";

/** 自定义片段类目标识（面板独立分组，与 i18n categories.custom 对应） */
export const CUSTOM_SNIPPET_CATEGORY = "custom";

/** 含 %CELLS% 标记的自定义模板派生网格默认行列（GridSizePicker 初始高亮） */
export const CUSTOM_SNIPPET_DEFAULT_GRID = Object.freeze({
  rows: 2,
  cols: 2,
});

/**
 * 归一化单个自定义条目为运行时形状：补齐 pkg / grid 字段，category 恒为
 * "custom"。宽松容错（字段缺失回落空值），供合并层与设置分区实时预览
 * 复用；持久化前的严格清洗走 preferences.sanitizeCustomSnippet。
 * @param {object|null} entry 存储条目 { name, insert, zh }（多余字段忽略）
 * @returns {{name: string, insert: string, zh: string, category: "custom", pkg: null, grid: {rows: number, cols: number}|null}}
 */
export function normalizeCustomSnippet(entry) {
  const name = typeof entry?.name === "string" ? entry.name : "";
  const insert = typeof entry?.insert === "string" ? entry.insert : "";
  const zh = typeof entry?.zh === "string" ? entry.zh : "";
  return {
    name,
    insert,
    zh,
    category: CUSTOM_SNIPPET_CATEGORY,
    pkg: null,
    grid: isGridTemplate(insert) ? { ...CUSTOM_SNIPPET_DEFAULT_GRID } : null,
  };
}

/**
 * 合并内建命令与自定义片段（三个消费入口的统一数据通道）：
 *   - 自定义条目逐条 normalizeCustomSnippet 后追加在内建之后；
 *   - 与内建命令同名的自定义条目丢弃（LaTeX 命令区分大小写，精确比较）；
 *   - base / custom 均容忍非数组入参（回落空），不修改任何入参数组。
 * @param {Array<{name: string}>} [base] 内建命令条目（默认 LATEX_COMMANDS）
 * @param {Array<{name: string, insert: string, zh?: string}>} [custom] 存储的自定义片段
 * @returns {Array} 合并后的条目数组（顺序：内建原始序 → 自定义归一化序）
 */
export function withCustomCommands(base = LATEX_COMMANDS, custom = []) {
  const baseList = Array.isArray(base) ? base : [];
  const customList = Array.isArray(custom) ? custom : [];
  if (customList.length === 0) return baseList.slice();
  const baseNames = new Set(
    baseList.map((entry) =>
      typeof entry?.name === "string" ? entry.name : "",
    ),
  );
  const merged = baseList.slice();
  for (const entry of customList) {
    const normalized = normalizeCustomSnippet(entry);
    if (baseNames.has(normalized.name)) continue;
    merged.push(normalized);
  }
  return merged;
}

/**
 * 命令面板分组：构建期类目沿用 groupCommandsByCategory 的声明顺序；
 * 自定义条目（category "custom"，不在构建期白名单内）归入独立分组置于
 * 末尾（三语条目 i18n categories.custom）。
 * @param {Array} commands 已合并的条目数组（withCustomCommands 产物）
 * @returns {{id: string, entries: Array}[]}
 */
export function groupCommandsWithCustom(commands) {
  const list = Array.isArray(commands) ? commands : [];
  const builtin = list.filter(
    (entry) => entry?.category !== CUSTOM_SNIPPET_CATEGORY,
  );
  const custom = list.filter(
    (entry) => entry?.category === CUSTOM_SNIPPET_CATEGORY,
  );
  const groups = groupCommandsByCategory(builtin);
  if (custom.length > 0) {
    groups.push({ id: CUSTOM_SNIPPET_CATEGORY, entries: custom });
  }
  return groups;
}
