/**
 * 多模型支持前端纯函数模块（multi-model §4 / §7）。
 *
 * 只含常量与纯函数，不依赖 i18n 运行时 / DOM / 网络，供设置分区组件、
 * 模型徽标组件与 node --test 单测共用。角色槽位与能力闭集必须与后端
 * backend/core/config.py 的 LLM_ROLE_KEYS / LLM_CAPABILITIES 保持一致。
 */

/** 内置默认档案的保留 id（由 llm 顶层字段合成，用户档案禁用） */
export const BUILTIN_PROFILE_ID = "__default__";

/** 固定槽位（角色）集合：assignments 只允许这些键，与后端同序 */
export const LLM_ROLE_KEYS = [
  "default",
  "agent",
  "ocr",
  "writing_chat",
  "typo_check",
  "expression_opt",
  "code_review",
  "terminology_check",
  "paragraph_translate",
];

/** 能力声明闭集：vision 参与 OCR 硬校验；reasoning 为提示性标注 */
export const LLM_CAPABILITIES = ["vision", "reasoning"];

/**
 * 两级槽位解析的目标档案 id（multi-model §4）：
 * assignments[role] 非空 → 该档案；为空 → assignments["default"]；
 * 仍为空 → 内置默认档案（BUILTIN_PROFILE_ID）。
 */
export function resolveRoleTarget(assignments, role) {
  const direct = (assignments && assignments[role]) || "";
  if (direct) return direct;
  return (assignments && assignments.default) || BUILTIN_PROFILE_ID;
}

/** 档案是否声明了某项能力（capabilities 非数组时视为无能力） */
export function hasCapability(profile, capability) {
  return (
    Array.isArray(profile?.capabilities) &&
    profile.capabilities.includes(capability)
  );
}

/** 过滤出声明了某项能力的档案（保持原顺序；入参非数组时返回空数组） */
export function filterProfilesByCapability(profiles, capability) {
  return (Array.isArray(profiles) ? profiles : []).filter((profile) =>
    hasCapability(profile, capability),
  );
}

/**
 * 构建槽位下拉选项（按能力过滤）。
 *
 * 返回 { value, kind, profile? }[]：
 * - kind: "builtin"（内置档案）| "followDefault"（跟随 default）| "profile"；
 * - value 为 assignments 中的存储值（builtin / followDefault 均为 ""）；
 * - id 为空的新建草稿档案不出现在选项中（尚不可被指派）；
 * - 非 default 槽首位为「跟随 default」空选项；default 槽首位为
 *   「使用全局默认配置」（内置档案）空选项；
 * - ocr 槽仅列出含 vision 的档案；default 槽在 ocr 槽为空（跟随 default）
 *   时同样仅列出含 vision 的档案；ocr 槽的「跟随 default」仅在 default
 *   槽两级解析目标具备 vision（内置档案视为全能力）时提供——与后端
 *   两级解析硬校验完全同口径，避免用户保存时才撞 400。
 */
export function buildSlotOptions(profiles, assignments, role) {
  const list = (Array.isArray(profiles) ? profiles : []).filter(
    (profile) => profile && profile.id,
  );
  const toOptions = (eligible) =>
    eligible.map((profile) => ({
      value: profile.id,
      kind: "profile",
      profile,
    }));

  if (role === "default") {
    const ocrFollowsDefault = !((assignments && assignments.ocr) || "");
    const eligible = ocrFollowsDefault
      ? filterProfilesByCapability(list, "vision")
      : list;
    return [{ value: "", kind: "builtin" }, ...toOptions(eligible)];
  }

  const eligible =
    role === "ocr" ? filterProfilesByCapability(list, "vision") : list;
  const options = toOptions(eligible);
  if (role !== "ocr") {
    return [{ value: "", kind: "followDefault" }, ...options];
  }
  const defaultTarget = resolveRoleTarget(assignments, "default");
  const followAllowed =
    defaultTarget === BUILTIN_PROFILE_ID ||
    hasCapability(
      list.find((profile) => profile.id === defaultTarget),
      "vision",
    );
  return followAllowed
    ? [{ value: "", kind: "followDefault" }, ...options]
    : options;
}

/**
 * 统计引用某档案的槽位（按两级解析结果，非仅直接指派）：
 * 返回按 LLM_ROLE_KEYS 顺序的槽位数组，供档案卡「被槽位引用」
 * 标记与删除保护提示展示。
 */
export function collectSlotReferences(assignments, profileId) {
  if (!profileId) return [];
  return LLM_ROLE_KEYS.filter(
    (role) => resolveRoleTarget(assignments, role) === profileId,
  );
}

/** 槽位下拉中档案选项的展示文案：「名称（model）」；缺名称回退 id */
export function formatProfileOption(profile) {
  if (!profile) return "";
  const label = profile.name || profile.id || "";
  return profile.model ? `${label}（${profile.model}）` : label;
}

/** 在 /api/llm/routes 响应中查找指定槽位的解析结果；无匹配返回 null */
export function findRouteByRole(routes, role) {
  const list = Array.isArray(routes) ? routes : [];
  return list.find((route) => route && route.role === role) || null;
}

/**
 * 组装徽标展示文案：「档案名 · 模型名」。
 * 缺档案名时只显示模型名，反之亦然；两者皆缺返回 null（调用方不渲染徽标）。
 */
export function formatRouteDisplay(route) {
  if (!route) return null;
  const name =
    typeof route.profile_name === "string" ? route.profile_name.trim() : "";
  const model = typeof route.model === "string" ? route.model.trim() : "";
  if (name && model) return `${name} · ${model}`;
  return name || model || null;
}

/**
 * 档案是否完全未填写（id / 名称 / 连接三件套 / Key 全空）：
 * 「添加档案」后未做任何输入的空白草稿，保存前应过滤掉，
 * 否则后端校验以「id 不能为空」拒绝，用户面对的却是一张没填过的卡片。
 */
export function isBlankProfile(profile) {
  if (!profile) return false;
  const blank = (value) => typeof value === "string" && value.trim() === "";
  return (
    blank(profile.id) &&
    blank(profile.name) &&
    blank(profile.base_url) &&
    blank(profile.model) &&
    blank(profile.api_key)
  );
}

/** 过滤完全未填写的草稿档案（保存前调用；profiles 整表替换进载荷） */
export function dropBlankDraftProfiles(profiles) {
  return (Array.isArray(profiles) ? profiles : []).filter(
    (profile) => !isBlankProfile(profile),
  );
}
