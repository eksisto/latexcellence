import test from "node:test";
import assert from "node:assert/strict";

import {
  CUSTOM_SNIPPET_CATEGORY,
  CUSTOM_SNIPPET_DEFAULT_GRID,
  groupCommandsWithCustom,
  normalizeCustomSnippet,
  withCustomCommands,
} from "../src/lib/editor/customCommands.js";
import {
  COMMAND_CATEGORIES,
  LATEX_COMMANDS,
  groupCommandsByCategory,
} from "../src/data/latexCommands/index.js";
import {
  expandEntryInsert,
  renderSnippet,
} from "../src/lib/editor/snippetInsert.js";
import {
  CUSTOM_SNIPPET_INSERT_MAX,
  CUSTOM_SNIPPETS_LIMIT,
  sanitizeCustomSnippet,
  sanitizeCustomSnippets,
} from "../src/lib/editor/preferences.js";

const validEntry = {
  name: "myenv",
  insert: "\\begin{myenv}\n\t$1\n\\end{myenv}",
  zh: "我的环境",
};

/* ── 存储条目清洗（preferences.sanitizeCustomSnippet / sanitizeCustomSnippets） ── */

test("sanitizeCustomSnippet 接受合法条目并强制 category、剥离多余字段", () => {
  const cleaned = sanitizeCustomSnippet({
    ...validEntry,
    category: "hacked",
    pkg: "amsmath",
    grid: { rows: 9, cols: 9 },
    evil: true,
  });
  assert.deepEqual(cleaned, {
    name: "myenv",
    insert: validEntry.insert,
    zh: "我的环境",
    category: "custom",
  });
  assert.equal(cleaned.category, CUSTOM_SNIPPET_CATEGORY);
});

test("sanitizeCustomSnippet 校验命令名形状（去首尾空白后匹配命令名规则）", () => {
  assert.equal(
    sanitizeCustomSnippet({ ...validEntry, name: "  myenv " }).name,
    "myenv",
  );
  assert.equal(sanitizeCustomSnippet({ ...validEntry, name: "\\alpha" }), null);
  assert.equal(sanitizeCustomSnippet({ ...validEntry, name: "1abc" }), null);
  assert.equal(sanitizeCustomSnippet({ ...validEntry, name: "a b" }), null);
  assert.equal(sanitizeCustomSnippet({ ...validEntry, name: "" }), null);
  assert.equal(sanitizeCustomSnippet({ ...validEntry, name: null }), null);
  // 星号变体与数字合法（与内建 name 校验同规则）
  assert.ok(sanitizeCustomSnippet({ name: "myenv*", insert: "x", zh: "" }));
  assert.ok(sanitizeCustomSnippet({ name: "my2", insert: "x", zh: "" }));
});

test("sanitizeCustomSnippet 校验插入模板：非空且不超长，截断不可取（整条丢弃）", () => {
  assert.equal(sanitizeCustomSnippet({ name: "a", insert: "", zh: "" }), null);
  assert.equal(sanitizeCustomSnippet({ name: "a", insert: 42, zh: "" }), null);
  assert.equal(
    sanitizeCustomSnippet({
      name: "a",
      insert: "x".repeat(CUSTOM_SNIPPET_INSERT_MAX + 1),
      zh: "",
    }),
    null,
  );
  // 恰好在上限的模板保留（LaTeX 模板含换行/制表符原样保留，不 trim）
  const maxInsert = "\t" + "x".repeat(CUSTOM_SNIPPET_INSERT_MAX - 1);
  assert.equal(
    sanitizeCustomSnippet({ name: "a", insert: maxInsert, zh: "" }).insert,
    maxInsert,
  );
});

test("sanitizeCustomSnippet 对中文名宽松：非字符串回落空串，留空不影响英文名检索", () => {
  assert.equal(
    sanitizeCustomSnippet({ name: "a", insert: "x", zh: 123 }).zh,
    "",
  );
  assert.equal(sanitizeCustomSnippet({ name: "a", insert: "x" }).zh, "");
  assert.equal(
    sanitizeCustomSnippet({ name: "a", insert: "x", zh: " 矩阵 " }).zh,
    "矩阵",
  );
});

test("sanitizeCustomSnippet 拒绝非对象条目", () => {
  assert.equal(sanitizeCustomSnippet(null), null);
  assert.equal(sanitizeCustomSnippet("x"), null);
  assert.equal(sanitizeCustomSnippet(42), null);
  assert.equal(sanitizeCustomSnippet(["myenv"]), null);
});

test("sanitizeCustomSnippets 按名去重（先到先得）", () => {
  const result = sanitizeCustomSnippets([
    { name: "dup", insert: "first", zh: "一" },
    { name: "dup", insert: "second", zh: "二" },
    validEntry,
  ]);
  assert.deepEqual(
    result.map((e) => e.insert),
    ["first", validEntry.insert],
  );
});

test("sanitizeCustomSnippets 超过上限截断（先到先得）", () => {
  const many = Array.from({ length: CUSTOM_SNIPPETS_LIMIT + 5 }, (_, i) => ({
    name: `snip${i}`,
    insert: "x",
    zh: "",
  }));
  const result = sanitizeCustomSnippets(many);
  assert.equal(result.length, CUSTOM_SNIPPETS_LIMIT);
  assert.equal(result[0].name, "snip0");
});

test("sanitizeCustomSnippets 非法条目跳过、非数组入参回落空数组", () => {
  const result = sanitizeCustomSnippets([
    validEntry,
    null,
    "junk",
    { name: "bad" },
  ]);
  assert.equal(result.length, 1);
  assert.deepEqual(sanitizeCustomSnippets(null), []);
  assert.deepEqual(sanitizeCustomSnippets("nope"), []);
  assert.deepEqual(sanitizeCustomSnippets(undefined), []);
});

/* ── 归一化与合并（customCommands） ───────────────────────────────────────── */

test("normalizeCustomSnippet 补齐运行时字段：category/pkg 恒定，%CELLS% 派生默认网格", () => {
  const gridEntry = normalizeCustomSnippet({
    name: "mygrid",
    insert: "\\begin{tabular}{%COLS%}\n\t%CELLS%\n\\end{tabular}",
    zh: "我的表格",
    pkg: "should-be-stripped",
  });
  assert.deepEqual(gridEntry, {
    name: "mygrid",
    insert: "\\begin{tabular}{%COLS%}\n\t%CELLS%\n\\end{tabular}",
    zh: "我的表格",
    category: "custom",
    pkg: null,
    grid: { ...CUSTOM_SNIPPET_DEFAULT_GRID },
  });
  const plain = normalizeCustomSnippet({
    name: "plain",
    insert: "\\mycmd{$1}",
    zh: "",
  });
  assert.equal(plain.grid, null);
  assert.equal(plain.pkg, null);
  // 宽松容错：缺字段回落空值，不抛错（实时预览对半成品草稿可用）
  assert.deepEqual(normalizeCustomSnippet(null), {
    name: "",
    insert: "",
    zh: "",
    category: "custom",
    pkg: null,
    grid: null,
  });
});

test("withCustomCommands 空自定义返回基线内容副本（不改动入参）", () => {
  const merged = withCustomCommands(LATEX_COMMANDS, []);
  assert.equal(merged.length, LATEX_COMMANDS.length);
  assert.equal(merged[0], LATEX_COMMANDS[0], "基线条目按引用原样保留");
  assert.notEqual(
    merged,
    LATEX_COMMANDS,
    "返回副本，冻结的数据库数组不被外部持有",
  );
  assert.deepEqual(
    withCustomCommands(LATEX_COMMANDS, null),
    LATEX_COMMANDS.slice(),
  );
});

test("withCustomCommands 自定义条目归一化后追加在内建之后", () => {
  const merged = withCustomCommands(LATEX_COMMANDS, [validEntry]);
  assert.equal(merged.length, LATEX_COMMANDS.length + 1);
  assert.equal(merged.at(-1).name, "myenv");
  assert.equal(merged.at(-1).category, "custom");
  assert.equal(merged.at(-1).grid, null);
});

test("withCustomCommands 与内建同名的自定义条目丢弃（运行期命中歧义防护）", () => {
  const merged = withCustomCommands(LATEX_COMMANDS, [
    { name: "alpha", insert: "\\alpha-custom", zh: "冒牌希腊字母" },
    validEntry,
  ]);
  assert.equal(merged.length, LATEX_COMMANDS.length + 1);
  assert.equal(merged.filter((e) => e.name === "alpha").length, 1);
  assert.equal(merged.find((e) => e.name === "alpha").insert, "\\alpha");
});

test("withCustomCommands 容忍非数组入参", () => {
  assert.deepEqual(withCustomCommands(null, [validEntry]).length, 1);
  assert.deepEqual(
    withCustomCommands(LATEX_COMMANDS, "junk").length,
    LATEX_COMMANDS.length,
  );
});

/* ── 面板分组（groupCommandsWithCustom） ──────────────────────────────────── */

test("groupCommandsWithCustom 无自定义时与构建期分组一致", () => {
  assert.deepEqual(
    groupCommandsWithCustom(LATEX_COMMANDS.slice()),
    groupCommandsByCategory(LATEX_COMMANDS),
  );
});

test("groupCommandsWithCustom 自定义条目归入独立分组置尾（三语条目 categories.custom）", () => {
  const groups = groupCommandsWithCustom(
    withCustomCommands(LATEX_COMMANDS, [validEntry]),
  );
  const ids = groups.map((g) => g.id);
  assert.deepEqual(
    ids,
    COMMAND_CATEGORIES.concat(["custom"]),
    "custom 分组在全部构建期类目之后",
  );
  const customGroup = groups.at(-1);
  assert.equal(customGroup.entries.length, 1);
  assert.equal(customGroup.entries[0].name, "myenv");
});

test("groupCommandsWithCustom 无自定义时不产生空 custom 分组", () => {
  const ids = groupCommandsWithCustom(LATEX_COMMANDS.slice()).map((g) => g.id);
  assert.equal(ids.includes("custom"), false);
});

/* ── 端到端契约：网格标记对自定义条目天然生效（机制层零改动） ──────────────── */

test("自定义网格模板经 expandEntryInsert 按选定行列展开（%COLS%/%CELLS%）", () => {
  const entry = normalizeCustomSnippet({
    name: "mygrid",
    insert: "\\begin{mytab}{%COLS%}\n\t%CELLS%\n\\end{mytab}",
    zh: "",
  });
  const template = expandEntryInsert(entry, { rows: 2, cols: 3 });
  assert.equal(template.includes("%COLS%"), false);
  assert.equal(template.includes("%CELLS%"), false);
  const rendered = renderSnippet(template);
  assert.ok(rendered.text.includes("ccc"), "列格式串应按 3 列展开为 ccc");
  // 2×3 网格 → 6 个单元格占位符
  assert.equal(rendered.ranges.length, 6);
});
