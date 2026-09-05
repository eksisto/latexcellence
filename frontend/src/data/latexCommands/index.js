/**
 * LaTeX 命令数据库统一加载器（editor-design §6.1）。
 *
 * 数据按类目拆分为纯 JSON 文件（希腊字母 / 运算 / 关系 / 箭头 / 数学结构 /
 * 文本样式 / 包裹命令 / 章节 / 图表 / 数学环境 / 表格矩阵网格模板），本文件负责：
 *   - 汇总合并并做 schema 校验（name 唯一、insert 非空、category 白名单、
 *     grid 形状合法）；数据错误在加载时即抛出，由测试与构建第一时间暴露；
 *   - 导出 `LATEX_COMMANDS`（条目数组）与 `COMMAND_CATEGORIES`（面板分组
 *     展示顺序），供 matcher / 补全 / 命令面板共用。
 *
 * 条目 schema（editor-design §6.1，严格一致）：
 *   { name, insert, zh, category, pkg, grid }
 *   - insert：插入模板；`$1/$2…` 为占位符、`$0` 为退出位置；网格模板另含
 *     `%COLS%`（列格式串）与 `%CELLS%`（单元格骨架）两个生成标记，
 *     由 lib/editor/snippetInsert.js 在选定行列后展开；
 *   - grid：参数化模板的默认行列（选择器初始高亮），null 表示无网格选择器。
 */

import greek from "./greek.json" with { type: "json" };
import operators from "./operators.json" with { type: "json" };
import relations from "./relations.json" with { type: "json" };
import arrows from "./arrows.json" with { type: "json" };
import structures from "./structures.json" with { type: "json" };
import textStyles from "./textStyles.json" with { type: "json" };
import wrap from "./wrap.json" with { type: "json" };
import sections from "./sections.json" with { type: "json" };
import figures from "./figures.json" with { type: "json" };
import mathEnvironments from "./mathEnvironments.json" with { type: "json" };
import gridTemplates from "./gridTemplates.json" with { type: "json" };

/** 面板分组展示顺序（与 i18n categories.<id> 键一一对应） */
export const COMMAND_CATEGORIES = [
  "greek",
  "operator",
  "relation",
  "arrow",
  "structure",
  "text",
  "wrap",
  "section",
  "figure",
  "mathenv",
  "grid",
];

const CATEGORY_SET = new Set(COMMAND_CATEGORIES);

/** 网格选择器允许的最大行列（GridSizePicker 与校验共用上界） */
export const MAX_GRID_ROWS = 8;
export const MAX_GRID_COLS = 8;

const NAME_RE = /^[A-Za-z][A-Za-z0-9*]*$/;

/** 单条目 schema 校验；不合法直接抛错（数据缺陷应在测试/开发期暴露） */
function validateEntry(entry, source) {
  const where = `${source}#${entry?.name ?? "?"}`;
  if (!entry || typeof entry !== "object") {
    throw new Error(`latexCommands: 条目不是对象 ${where}`);
  }
  if (typeof entry.name !== "string" || !NAME_RE.test(entry.name)) {
    throw new Error(`latexCommands: name 非法 ${where}`);
  }
  if (typeof entry.insert !== "string" || entry.insert.length === 0) {
    throw new Error(`latexCommands: insert 非法 ${where}`);
  }
  if (typeof entry.zh !== "string" || entry.zh.length === 0) {
    throw new Error(`latexCommands: zh 非法 ${where}`);
  }
  if (!CATEGORY_SET.has(entry.category)) {
    throw new Error(`latexCommands: category 未知 ${where}`);
  }
  if (entry.pkg !== null && typeof entry.pkg !== "string") {
    throw new Error(`latexCommands: pkg 必须为 string|null ${where}`);
  }
  if (entry.grid !== null) {
    const g = entry.grid;
    const ok =
      g &&
      typeof g === "object" &&
      Number.isInteger(g.rows) &&
      Number.isInteger(g.cols) &&
      g.rows >= 1 &&
      g.cols >= 1 &&
      g.rows <= MAX_GRID_ROWS &&
      g.cols <= MAX_GRID_COLS;
    if (!ok) {
      throw new Error(`latexCommands: grid 行列非法 ${where}`);
    }
  }
}

function mergeCategory(entries, source) {
  for (const entry of entries) validateEntry(entry, source);
  return entries;
}

const merged = [
  ...mergeCategory(greek, "greek.json"),
  ...mergeCategory(operators, "operators.json"),
  ...mergeCategory(relations, "relations.json"),
  ...mergeCategory(arrows, "arrows.json"),
  ...mergeCategory(structures, "structures.json"),
  ...mergeCategory(textStyles, "textStyles.json"),
  ...mergeCategory(wrap, "wrap.json"),
  ...mergeCategory(sections, "sections.json"),
  ...mergeCategory(figures, "figures.json"),
  ...mergeCategory(mathEnvironments, "mathEnvironments.json"),
  ...mergeCategory(gridTemplates, "gridTemplates.json"),
];

// name 唯一性（LaTeX 命令区分大小写：\bmatrix 与 \Bmatrix 是两个命令）
const seenNames = new Set();
for (const entry of merged) {
  if (seenNames.has(entry.name)) {
    throw new Error(`latexCommands: name 重复 ${entry.name}`);
  }
  seenNames.add(entry.name);
}

/** 全量命令条目（冻结，防止运行期被意外改写） */
export const LATEX_COMMANDS = Object.freeze(merged.slice());

/** 按类目分组（顺序遵循 COMMAND_CATEGORIES），供面板分类列表使用 */
export function groupCommandsByCategory(commands = LATEX_COMMANDS) {
  const groups = COMMAND_CATEGORIES.map((id) => ({ id, entries: [] }));
  const byId = new Map(groups.map((g) => [g.id, g]));
  for (const entry of commands) {
    byId.get(entry.category)?.entries.push(entry);
  }
  return groups.filter((g) => g.entries.length > 0);
}
