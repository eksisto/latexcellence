import test from "node:test";
import assert from "node:assert/strict";

import {
  BUILTIN_PROFILE_ID,
  LLM_ROLE_KEYS,
  buildSlotOptions,
  collectSlotReferences,
  filterProfilesByCapability,
  findRouteByRole,
  dropBlankDraftProfiles,
  formatProfileOption,
  formatRouteDisplay,
  hasCapability,
  isBlankProfile,
  resolveRoleTarget,
} from "../src/utils/modelProfiles.js";

// 测试档案（结构与后端 ModelProfile 展示字段一致，不含 api_key 等敏感字段）
const visionPro = {
  id: "vision-pro",
  name: "视觉模型",
  model: "glm-4.6v",
  capabilities: ["vision"],
};
const deepthink = {
  id: "deepthink",
  name: "深度思考模型",
  model: "glm-5.3",
  capabilities: ["vision", "reasoning"],
};
const fast = {
  id: "fast",
  name: "快速模型",
  model: "deepseek-flash",
  capabilities: [],
};
const PROFILES = [visionPro, deepthink, fast];

// ── 能力过滤 ──────────────────────────────────────────────

test("hasCapability 对非数组 capabilities 视为无能力", () => {
  assert.equal(hasCapability(visionPro, "vision"), true);
  assert.equal(hasCapability(fast, "vision"), false);
  assert.equal(hasCapability({}, "vision"), false);
  assert.equal(hasCapability(null, "vision"), false);
  assert.equal(hasCapability({ capabilities: "vision" }, "vision"), false);
});

test("filterProfilesByCapability 保持原顺序且容忍非数组入参", () => {
  assert.deepEqual(filterProfilesByCapability(PROFILES, "vision"), [
    visionPro,
    deepthink,
  ]);
  assert.deepEqual(filterProfilesByCapability(PROFILES, "reasoning"), [
    deepthink,
  ]);
  assert.deepEqual(filterProfilesByCapability(undefined, "vision"), []);
  assert.deepEqual(filterProfilesByCapability("nope", "vision"), []);
});

// ── 两级槽位解析 ──────────────────────────────────────────

test("resolveRoleTarget 两级解析：指派槽 → default 槽 → 内置档案", () => {
  const assignments = { default: "deepthink", agent: "", ocr: "vision-pro" };
  assert.equal(resolveRoleTarget(assignments, "ocr"), "vision-pro");
  // agent 为空 → 跟随 default
  assert.equal(resolveRoleTarget(assignments, "agent"), "deepthink");
  // default 也为空 → 内置档案
  assert.equal(resolveRoleTarget({}, "default"), BUILTIN_PROFILE_ID);
  assert.equal(resolveRoleTarget(undefined, "agent"), BUILTIN_PROFILE_ID);
});

// ── 槽位选项构建（能力过滤 + 空选项语义）──────────────────

test("buildSlotOptions ocr 槽仅列出 vision 档案，首位为跟随 default", () => {
  const options = buildSlotOptions(PROFILES, {}, "ocr");
  assert.deepEqual(
    options.map((o) => [o.value, o.kind]),
    [
      ["", "followDefault"],
      ["vision-pro", "profile"],
      ["deepthink", "profile"],
    ],
  );
  // id 为空的草稿档案不出现在选项中
  const drafts = buildSlotOptions(
    [{ id: "", name: "未命名", model: "x", capabilities: ["vision"] }],
    {},
    "ocr",
  );
  assert.deepEqual(
    drafts.map((o) => o.kind),
    ["followDefault"],
  );
});

test("buildSlotOptions ocr 槽跟随 default 选项与后端 vision 硬校验同口径", () => {
  // default 槽解析目标（直接指派 fast，缺 vision）→「跟随 default」会被
  // 后端两级解析校验拒绝，故不提供该空选项
  const blocked = buildSlotOptions(
    PROFILES,
    { default: "fast", ocr: "" },
    "ocr",
  );
  assert.deepEqual(
    blocked.map((o) => [o.value, o.kind]),
    [
      ["vision-pro", "profile"],
      ["deepthink", "profile"],
    ],
  );
  // default 指派 vision 档案 → 允许跟随
  const allowed = buildSlotOptions(
    PROFILES,
    { default: "vision-pro", ocr: "" },
    "ocr",
  );
  assert.deepEqual(
    allowed.map((o) => [o.value, o.kind]),
    [
      ["", "followDefault"],
      ["vision-pro", "profile"],
      ["deepthink", "profile"],
    ],
  );
  // default 为空（两级解析落到内置档案，视为全能力）→ 允许跟随
  assert.equal(buildSlotOptions(PROFILES, {}, "ocr")[0].kind, "followDefault");
  // default 指派了不存在的档案 id（悬挂引用，保存必被拒）→ 保守不提供跟随
  const dangling = buildSlotOptions(PROFILES, { default: "ghost" }, "ocr");
  assert.deepEqual(
    dangling.map((o) => o.kind),
    ["profile", "profile"],
  );
});

test("buildSlotOptions 非 ocr 非 default 槽列出全部档案", () => {
  const options = buildSlotOptions(
    PROFILES,
    { ocr: "vision-pro" },
    "typo_check",
  );
  assert.deepEqual(
    options.map((o) => [o.value, o.kind]),
    [
      ["", "followDefault"],
      ["vision-pro", "profile"],
      ["deepthink", "profile"],
      ["fast", "profile"],
    ],
  );
});

test("buildSlotOptions default 槽首位为内置档案选项", () => {
  // ocr 已显式指派 vision 档案时，default 槽不受 vision 约束
  const options = buildSlotOptions(
    PROFILES,
    { ocr: "vision-pro", default: "fast" },
    "default",
  );
  assert.deepEqual(
    options.map((o) => [o.value, o.kind]),
    [
      ["", "builtin"],
      ["vision-pro", "profile"],
      ["deepthink", "profile"],
      ["fast", "profile"],
    ],
  );
});

test("buildSlotOptions ocr 跟随 default 时 default 槽同样仅列 vision 档案", () => {
  // 与后端两级解析硬校验同口径：ocr 为空时经 default 解析，
  // default 指派非 vision 档案会被拒绝，故选项直接过滤
  const options = buildSlotOptions(PROFILES, { ocr: "" }, "default");
  assert.deepEqual(
    options.map((o) => [o.value, o.kind]),
    [
      ["", "builtin"],
      ["vision-pro", "profile"],
      ["deepthink", "profile"],
    ],
  );
  // 无任何 vision 档案时仅剩内置选项
  const only = buildSlotOptions([fast], {}, "default");
  assert.deepEqual(
    only.map((o) => o.kind),
    ["builtin"],
  );
});

// ── 档案引用统计（删除保护）───────────────────────────────

test("collectSlotReferences 按两级解析统计引用槽位", () => {
  // 后端加载时会补全全部槽位键（缺失补空串），测试夹具保持同口径
  const assignments = {
    default: "deepthink",
    agent: "",
    ocr: "vision-pro",
    writing_chat: "",
    typo_check: "",
    expression_opt: "",
    code_review: "",
    terminology_check: "",
    paragraph_translate: "",
  };
  // 直接引用
  assert.deepEqual(collectSlotReferences(assignments, "vision-pro"), ["ocr"]);
  // 其余槽位为空，经 default 间接解析到 deepthink
  assert.deepEqual(collectSlotReferences(assignments, "deepthink"), [
    "default",
    "agent",
    "writing_chat",
    "typo_check",
    "expression_opt",
    "code_review",
    "terminology_check",
    "paragraph_translate",
  ]);
  // 无引用
  assert.deepEqual(collectSlotReferences(assignments, "fast"), []);
  // 空 id 防御
  assert.deepEqual(collectSlotReferences(assignments, ""), []);
});

// ── 展示文案组装 ──────────────────────────────────────────

test("formatProfileOption 输出 名称（model），缺名称回退 id", () => {
  assert.equal(formatProfileOption(visionPro), "视觉模型（glm-4.6v）");
  assert.equal(formatProfileOption({ id: "p1", model: "m1" }), "p1（m1）");
  assert.equal(formatProfileOption({ id: "p1" }), "p1");
  assert.equal(formatProfileOption(null), "");
});

test("findRouteByRole 按 role 查找，无匹配返回 null", () => {
  const routes = [
    { role: "ocr", profile_name: "视觉模型", model: "glm-4.6v" },
    { role: "default", profile_name: "全局默认模型", model: "deepseek-flash" },
  ];
  assert.equal(findRouteByRole(routes, "ocr"), routes[0]);
  assert.equal(findRouteByRole(routes, "agent"), null);
  assert.equal(findRouteByRole(undefined, "ocr"), null);
  assert.equal(findRouteByRole([null], "ocr"), null);
});

test("formatRouteDisplay 组装「档案名 · 模型名」，缺项回退、全缺不渲染", () => {
  assert.equal(
    formatRouteDisplay({ profile_name: "视觉模型", model: "glm-4.6v" }),
    "视觉模型 · glm-4.6v",
  );
  // 空白视为缺失
  assert.equal(formatRouteDisplay({ profile_name: "  ", model: "m" }), "m");
  assert.equal(formatRouteDisplay({ profile_name: "n", model: "" }), "n");
  assert.equal(formatRouteDisplay({ profile_name: "n" }), "n");
  assert.equal(formatRouteDisplay({ model: "m" }), "m");
  assert.equal(formatRouteDisplay({}), null);
  assert.equal(formatRouteDisplay(null), null);
});

// ── 常量与后端契约的一致性 ────────────────────────────────

test("角色槽位闭集与计划 §6 的 9 个槽位一致", () => {
  assert.deepEqual(LLM_ROLE_KEYS, [
    "default",
    "agent",
    "ocr",
    "writing_chat",
    "typo_check",
    "expression_opt",
    "code_review",
    "terminology_check",
    "paragraph_translate",
  ]);
  assert.equal(BUILTIN_PROFILE_ID, "__default__");
});

// ── 空白草稿过滤（保存前清理「添加档案」后未做任何输入的卡片）──────────

test("isBlankProfile：仅当 id/名称/连接三件套/Key 全空（含纯空白）时为真", () => {
  assert.equal(
    isBlankProfile({ id: "", name: "", base_url: "", model: "", api_key: "" }),
    true,
  );
  assert.equal(
    isBlankProfile({
      id: "  ",
      name: "",
      base_url: "\t",
      model: "",
      api_key: "",
    }),
    true,
  );
  assert.equal(isBlankProfile(null), false);
  assert.equal(isBlankProfile(undefined), false);
  // 任一字段有值即非空白草稿
  assert.equal(
    isBlankProfile({ id: "", name: "x", base_url: "", model: "", api_key: "" }),
    false,
  );
  assert.equal(
    isBlankProfile({
      id: "p1",
      name: "",
      base_url: "",
      model: "",
      api_key: "",
    }),
    false,
  );
  assert.equal(
    isBlankProfile({ id: "", name: "", base_url: "", model: "m", api_key: "" }),
    false,
  );
  // 掩码 Key 原样回填的已保存档案绝不会被误判为空白
  assert.equal(
    isBlankProfile({
      id: "p1",
      name: "n",
      base_url: "u",
      model: "m",
      api_key: "***abcd",
    }),
    false,
  );
});

test("dropBlankDraftProfiles：仅剔除空白草稿，保留有效档案（含掩码 Key）", () => {
  const blank = { id: "", name: "", base_url: "", model: "", api_key: "" };
  const saved = {
    id: "p1",
    name: "n",
    base_url: "u",
    model: "m",
    api_key: "***abcd",
  };
  const partial = {
    id: "",
    name: "起名中",
    base_url: "",
    model: "",
    api_key: "",
  };
  assert.deepEqual(dropBlankDraftProfiles([blank, saved, partial]), [
    saved,
    partial,
  ]);
  // 非数组入参防御
  assert.deepEqual(dropBlankDraftProfiles(undefined), []);
});
