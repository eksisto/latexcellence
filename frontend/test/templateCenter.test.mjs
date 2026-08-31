import test from "node:test";
import assert from "node:assert/strict";

import {
  TEMPLATE_CATEGORIES,
  isValidCategory,
  normalizeCategory,
  categoryLabelKey,
  filterTemplates,
  sortTemplates,
  isViewableTemplateFile,
  buildTemplateFileTree,
} from "../src/utils/templateCenter.js";

// 分类枚举与后端 TemplateCategory Literal 严格一致（设计决策，不得扩展）
test("TEMPLATE_CATEGORIES 为固定九类枚举", () => {
  assert.deepEqual(TEMPLATE_CATEGORIES, [
    "paper",
    "thesis",
    "report",
    "slides",
    "resume",
    "book",
    "poster",
    "notes",
    "other",
  ]);
});

test("isValidCategory / normalizeCategory 折叠非法分类为 other", () => {
  assert.equal(isValidCategory("paper"), true);
  assert.equal(isValidCategory("other"), true);
  assert.equal(isValidCategory("article"), false);
  assert.equal(isValidCategory(undefined), false);
  assert.equal(normalizeCategory("slides"), "slides");
  assert.equal(normalizeCategory("novel"), "other");
  assert.equal(normalizeCategory(""), "other");
  assert.equal(normalizeCategory(null), "other");
});

test("categoryLabelKey 返回 pages 命名空间下的键", () => {
  assert.equal(categoryLabelKey("thesis"), "templateCenter.categories.thesis");
  assert.equal(categoryLabelKey("bogus"), "templateCenter.categories.other");
});

test("filterTemplates 按关键字与分类过滤", () => {
  const templates = [
    {
      name: "IEEE 会议论文",
      name_en: "IEEE Conference",
      description: "双栏会议模板",
      category: "paper",
    },
    {
      name: "硕士学位论文",
      name_en: "",
      description: "含章节与参考文献",
      category: "thesis",
    },
    {
      name: "简明简历",
      name_en: "CV",
      description: "",
      category: "resume",
    },
  ];

  // 关键字命中 name / name_en / description（不区分大小写）
  assert.equal(filterTemplates(templates, { keyword: "ieee" }).length, 1);
  assert.equal(filterTemplates(templates, { keyword: "CV" }).length, 1);
  assert.equal(filterTemplates(templates, { keyword: "参考文献" }).length, 1);
  assert.equal(filterTemplates(templates, { keyword: "  会议  " }).length, 1);
  // 空白关键字不过滤
  assert.equal(filterTemplates(templates, { keyword: "   " }).length, 3);
  // 分类精确过滤 + 关键字叠加
  assert.equal(filterTemplates(templates, { category: "paper" }).length, 1);
  assert.equal(
    filterTemplates(templates, { keyword: "论文", category: "thesis" }).length,
    1,
  );
  assert.equal(
    filterTemplates(templates, { keyword: "ieee", category: "thesis" }).length,
    0,
  );
  // 非法输入容忍
  assert.deepEqual(filterTemplates(null, {}), []);
});

test("sortTemplates 内置在前，同类按 created_at 倒序且不改入参", () => {
  const templates = [
    { kind: "user", name: "旧模板", created_at: "2024-01-01" },
    { kind: "user", name: "新模板", created_at: "2025-06-01" },
    { kind: "builtin", name: "内置B", created_at: "2020-01-01" },
    { kind: "builtin", name: "内置A", created_at: "2026-01-01" },
    { kind: "user", name: "无日期", created_at: "" },
  ];
  const sorted = sortTemplates(templates);
  assert.deepEqual(
    sorted.map((t) => t.name),
    ["内置A", "内置B", "新模板", "旧模板", "无日期"],
  );
  // 原数组不被修改
  assert.equal(templates[0].name, "旧模板");
});

test("isViewableTemplateFile 与后端文本白名单一致", () => {
  assert.equal(isViewableTemplateFile("main.tex"), true);
  assert.equal(isViewableTemplateFile("styles/custom.STY"), true);
  assert.equal(isViewableTemplateFile("refs.bib"), true);
  assert.equal(isViewableTemplateFile("notes.md"), true);
  assert.equal(isViewableTemplateFile("assets/figure.png"), false);
  assert.equal(isViewableTemplateFile("assets/preview.pdf"), false);
  assert.equal(isViewableTemplateFile(""), false);
});

test("buildTemplateFileTree 目录在前、同层按名称排序", () => {
  const tree = buildTemplateFileTree([
    { path: "sections/method.tex" },
    "main.tex",
    "sections/intro.tex",
    "figures/overview.png",
    "refs.bib",
  ]);
  // 顶层：目录 figures / sections 在前，文件 main.tex / refs.bib 在后
  assert.deepEqual(
    tree.map((n) => `${n.name}${n.isDir ? "/" : ""}`),
    ["figures/", "sections/", "main.tex", "refs.bib"],
  );
  const sections = tree.find((n) => n.name === "sections");
  assert.equal(sections.isDir, true);
  assert.deepEqual(
    sections.children.map((n) => n.name),
    ["intro.tex", "method.tex"],
  );
  const intro = sections.children[0];
  assert.equal(intro.isDir, false);
  assert.equal(intro.path, "sections/intro.tex");
  assert.deepEqual(intro.children, []);
});

test("buildTemplateFileTree 容忍空输入与畸形路径", () => {
  assert.deepEqual(buildTemplateFileTree([]), []);
  assert.deepEqual(buildTemplateFileTree(null), []);
  assert.deepEqual(buildTemplateFileTree([null, "", { path: "" }]), []);
  const tree = buildTemplateFileTree(["a/b/c.tex"]);
  assert.equal(tree[0].name, "a");
  assert.equal(tree[0].children[0].children[0].path, "a/b/c.tex");
});
