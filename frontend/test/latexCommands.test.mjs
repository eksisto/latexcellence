import test from "node:test";
import assert from "node:assert/strict";

import {
  COMMAND_CATEGORIES,
  LATEX_COMMANDS,
  MAX_GRID_COLS,
  MAX_GRID_ROWS,
  groupCommandsByCategory,
} from "../src/data/latexCommands/index.js";
import { extractEnvironments } from "../src/lib/editor/latexCompletion.js";
import { WRAP_DIRECT_SHORTCUTS } from "../src/lib/editor/wrap.js";

/** 数据库条目的合法宏包白名单（§6.5 最小映射集，新增需有意扩充） */
const KNOWN_PACKAGES = new Set(["amsmath", "amssymb", "booktabs", "graphicx"]);

test("首批命令数据库为精选百余条且结构冻结", () => {
  assert.ok(Array.isArray(LATEX_COMMANDS));
  assert.ok(LATEX_COMMANDS.length >= 100, "首批应不少于 100 条");
  assert.ok(Object.isFrozen(LATEX_COMMANDS));
});

test("每条目 schema 与计划一致：{name, insert, zh, category, pkg, grid}", () => {
  const schemaKeys = ["name", "insert", "zh", "category", "pkg", "grid"].sort();
  for (const entry of LATEX_COMMANDS) {
    assert.deepEqual(
      Object.keys(entry).sort(),
      schemaKeys,
      `${entry?.name} 字段与 schema 不符`,
    );
    assert.match(entry.name, /^[A-Za-z][A-Za-z0-9*]*$/);
    assert.equal(typeof entry.insert, "string");
    assert.ok(entry.insert.length > 0);
    assert.equal(typeof entry.zh, "string");
    assert.ok(entry.zh.length > 0);
    assert.ok(COMMAND_CATEGORIES.includes(entry.category));
    if (entry.pkg !== null) {
      assert.equal(typeof entry.pkg, "string");
      assert.ok(
        KNOWN_PACKAGES.has(entry.pkg),
        `${entry.name} 的宏包 ${entry.pkg} 不在最小映射集内`,
      );
    }
  }
});

test("name 全库唯一（区分大小写：bmatrix 与 Bmatrix 并存）", () => {
  const names = LATEX_COMMANDS.map((e) => e.name);
  assert.equal(new Set(names).size, names.length);
  // 大小写敏感的代表性样本
  assert.ok(names.includes("bmatrix"));
  assert.ok(names.includes("Bmatrix"));
});

test("网格模板标记与 grid 配置一一对应", () => {
  for (const entry of LATEX_COMMANDS) {
    const hasCells = entry.insert.includes("%CELLS%");
    const hasCols = entry.insert.includes("%COLS%");
    if (entry.grid) {
      // 单元格骨架标记必有；列格式标记仅表格/数组类需要（矩阵家族无列参数）
      assert.ok(hasCells, `${entry.name} 带 grid 却缺 %CELLS% 标记`);
      if (hasCols) assert.ok(entry.name !== "cases");
    } else {
      assert.ok(!hasCells && !hasCols, `${entry.name} 无 grid 却含生成标记`);
    }
  }
});

test("grid 配置行列在合法区间内", () => {
  for (const entry of LATEX_COMMANDS) {
    if (!entry.grid) continue;
    assert.ok(
      entry.grid.rows >= 1 && entry.grid.rows <= MAX_GRID_ROWS,
      `${entry.name} 行数越界`,
    );
    assert.ok(
      entry.grid.cols >= 1 && entry.grid.cols <= MAX_GRID_COLS,
      `${entry.name} 列数越界`,
    );
  }
});

test("计划点名的网格条目齐备且必需宏包指向正确", () => {
  const byName = new Map(LATEX_COMMANDS.map((e) => [e.name, e]));
  for (const name of [
    "tabular",
    "matrix",
    "pmatrix",
    "bmatrix",
    "vmatrix",
    "cases",
  ]) {
    const entry = byName.get(name);
    assert.ok(entry, `缺少 ${name}`);
    assert.ok(entry.grid, `${name} 应带网格配置`);
  }
  for (const name of [
    "matrix",
    "pmatrix",
    "bmatrix",
    "vmatrix",
    "cases",
    "align",
  ]) {
    assert.equal(byName.get(name)?.pkg, "amsmath", `${name} 应指向 amsmath`);
  }
  assert.equal(byName.get("threelinetable")?.pkg, "booktabs");
});

test("宏包归属交叉核验：amsmath/amssymb 专属命令的 pkg 字段正确", () => {
  const byName = new Map(LATEX_COMMANDS.map((e) => [e.name, e]));
  // \iint 与 \binom 由 amsmath 定义，内核 LaTeX 无此命令
  for (const name of ["iint", "binom", "dfrac", "tfrac"]) {
    assert.equal(byName.get(name)?.pkg, "amsmath", `${name} 应指向 amsmath`);
  }
  // \varkappa 是 amssymb 符号（amsmath 不定义）
  assert.equal(byName.get("varkappa")?.pkg, "amssymb");
});

test("闭合曲线积分使用内核 \\oint；esint 专属 \\ooint 不入库", () => {
  const entry = LATEX_COMMANDS.find((e) => e.name === "oint");
  assert.ok(entry, "缺少 oint 条目");
  assert.ok(entry.insert.startsWith("\\oint"));
  assert.equal(entry.pkg, null, "\\oint 属内核命令，无需宏包");
  assert.ok(
    !LATEX_COMMANDS.some((e) => e.name === "ooint"),
    "\\ooint 需 esint 宏包，不在最小映射集内",
  );
});

test("环境清单可从数据库提取且 begin/end 成对", () => {
  const envs = extractEnvironments();
  assert.ok(envs.length >= 10);
  const seen = new Set();
  for (const { name, entry } of envs) {
    assert.ok(!seen.has(name), `环境 ${name} 重复提取`);
    seen.add(name);
    assert.ok(entry.insert.includes(`\\begin{${name}}`));
    assert.ok(entry.insert.includes(`\\end{${name}}`));
  }
});

test("分组保持类目声明顺序且组内保持传入顺序", () => {
  const groups = groupCommandsByCategory();
  const ids = groups.map((g) => g.id);
  const declaredOrder = COMMAND_CATEGORIES.filter((id) => ids.includes(id));
  assert.deepEqual(ids, declaredOrder);
  for (const group of groups) {
    for (const entry of group.entries) {
      assert.equal(entry.category, group.id);
    }
  }
});

test("wrap 类目收录全部包裹命令（含直包四命令），insert 为单参数模板", () => {
  const wrapEntries = LATEX_COMMANDS.filter((e) => e.category === "wrap");
  assert.ok(wrapEntries.length >= 10, "wrap 全集应不少于 10 条");
  const names = new Set(wrapEntries.map((e) => e.name));
  // 决策 Q13：直包命令必须在 wrap 类目内
  for (const name of WRAP_DIRECT_SHORTCUTS.map((s) => s.command)) {
    assert.ok(names.has(name), `wrap 类目缺少直包命令 ${name}`);
  }
  for (const entry of wrapEntries) {
    assert.match(
      entry.insert,
      new RegExp(`^\\\\${entry.name}\\{\\$1\\}\\$0$`),
      `${entry.name} 的 insert 应为单参数包裹模板`,
    );
    assert.equal(entry.pkg, null, "包裹命令均属内核 LaTeX，无需宏包");
  }
  // 原文本样式类目的包裹命令已整体迁至 wrap 类目（name 全库唯一）
  assert.ok(!names.has("text"), "\\text 属行内文本（text 类目），不入 wrap");
  assert.ok(
    LATEX_COMMANDS.some((e) => e.name === "text" && e.category === "text"),
  );
});
