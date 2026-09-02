import { memo, useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import ConfigSection, {
  CheckboxField,
} from "@/components/settings/ConfigSection";
import ChevronDown from "lucide-react/dist/esm/icons/chevron-down.mjs";
import ChevronRight from "lucide-react/dist/esm/icons/chevron-right.mjs";
import Plus from "lucide-react/dist/esm/icons/plus.mjs";
import Trash2 from "lucide-react/dist/esm/icons/trash-2.mjs";
import {
  buildSlotOptions,
  collectSlotReferences,
  formatProfileOption,
  LLM_CAPABILITIES,
  LLM_ROLE_KEYS,
} from "@/utils/modelProfiles";

/** 槽位下拉的空选项哨兵值：Base UI Select 对 "" 走 placeholder 语义，
 * 故以 "@" 前缀哨兵承载空指派（档案 id 仅允许字母/数字/下划线/连字符，
 * 不可能与哨兵冲突）；回写 assignments 时还原为 "" */
const EMPTY_BUILTIN = "@builtin";
const EMPTY_FOLLOW = "@follow";

/** 草稿档案卡片的稳定列表键（Symbol 属性）：随对象展开复制、JSON 序列化
 * 自动剔除（不会进入保存载荷），使卡片键与 id 输入解耦——否则以 id 为键
 * 时草稿每敲一个字符就重挂载一次，输入焦点与卡片内草稿态全部丢失 */
const DRAFT_CARD_KEY = Symbol("draftCardKey");
let draftCardKeySeq = 0;

/** 解析 JSON 对象；空串视为 {}（继承全局）；非法或非对象返回 undefined（不传播） */
function parseExtraBody(raw) {
  const text = raw.trim();
  if (!text) return {};
  try {
    const parsed = JSON.parse(text);
    if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
      return parsed;
    }
  } catch {
    /* fall through */
  }
  return undefined;
}

/** 判断档案是否覆盖了任一可选项（用于折叠区收起时的"已覆盖"标记） */
function hasActiveOverride(profile) {
  return (
    profile.temperature != null ||
    profile.max_tokens != null ||
    // 后端契约：request_interval 为 0 / null 时均继承顶层
    // （llm_client._resolve_cfg 的 `profile.request_interval or 顶层`），
    // 故 0 不算"已覆盖"，与置空显示口径一致
    (profile.request_interval ?? 0) > 0 ||
    profile.max_concurrency != null ||
    (profile.context_window_tokens ?? 0) > 0 ||
    !!profile.models_probe_url ||
    !!(profile.extra_body && Object.keys(profile.extra_body).length > 0)
  );
}

/**
 * 可选覆盖项共享数字输入（草稿态承载自由输入，空 = 继承全局）。
 *
 * 与 NumberField 的差异：继承语义要求字段可表达"空"（null），因此
 * 不做失焦归一化到默认值，而是空值提交 null（后端 null = 继承顶层）；
 * 非法输入失焦时回滚为当前值。
 */
function OverrideNumberField({ label, value, onCommit, min, max, step }) {
  const [draft, setDraft] = useState(value == null ? "" : String(value));

  // 外部值变化（如重置/保存后回填）时同步草稿
  useEffect(() => {
    setDraft(value == null ? "" : String(value));
  }, [value]);

  const commit = () => {
    const text = draft.trim();
    if (text === "") {
      onCommit(null);
      return;
    }
    const num = Number(text);
    if (!Number.isFinite(num)) {
      setDraft(value == null ? "" : String(value));
      return;
    }
    let safe = num;
    if (min != null && safe < Number(min)) safe = Number(min);
    if (max != null && safe > Number(max)) safe = Number(max);
    onCommit(safe);
    setDraft(String(safe));
  };

  return (
    <div className="space-y-1">
      <Label className="text-xs">{label}</Label>
      <Input
        type="number"
        min={min}
        max={max}
        step={step}
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={commit}
      />
    </div>
  );
}

/**
 * 单个模型档案卡片：连接三件套 + 能力勾选 + 可选覆盖项折叠区。
 *
 * api_key 沿用全局掩码协议（GET 返回 ***xxxx 掩码；保留掩码保存 =
 * 后端按档案 id 沿用原值；清空 = 移除）。已保存档案的 id 只读——
 * 掩码 Key 按档案 id 匹配保留原值，改 id 会导致后端匹配不到而清空 Key。
 */
function ProfileCard({
  profile,
  cardKey,
  savedIds,
  referencedRoles,
  onPatch,
  onPatchId,
  onRemove,
  onExtraBodyInvalidChange,
}) {
  const { t } = useTranslation("components");
  // 已保存档案 id 只读（挂载时快照判定）：掩码 api_key 按档案 id 匹配保留
  // 原值，改 id 会让后端匹配不到而清空 Key。草稿卡片挂载时 id 必为空串、
  // 永不锁定——输入撞上已保存 id 时仍可继续编辑，唯一性冲突由保存期
  // 后端校验拒绝；卡片键稳定（见 profiles.map），快照与卡片一一对应
  const lockedIdRef = useRef(null);
  if (lockedIdRef.current === null) {
    lockedIdRef.current = savedIds.has(profile.id);
  }
  const lockedId = lockedIdRef.current;
  const [showOverrides, setShowOverrides] = useState(false);
  // extra_body 草稿：编辑中显示原始文本，仅在合法 JSON 时向父级传播对象
  const [extraBodyDraft, setExtraBodyDraft] = useState(null);
  const extraBodyValid =
    extraBodyDraft === null || parseExtraBody(extraBodyDraft) !== undefined;

  const extraBodyText =
    extraBodyDraft ??
    (profile.extra_body && Object.keys(profile.extra_body).length > 0
      ? JSON.stringify(profile.extra_body, null, 2)
      : "");

  function handleExtraBodyChange(raw) {
    setExtraBodyDraft(raw);
    const parsed = parseExtraBody(raw);
    if (parsed !== undefined) {
      onPatch({ extra_body: parsed });
    }
  }

  // extra_body 草稿非法时向分区登记（保存按钮据此禁用）：非法时父级仍持有
  // 上一个合法对象，放行保存会静默落盘与界面显示不一致的数据；转回合法或
  // 卡片卸载时经 effect cleanup 注销，删除卡片不会留下悬挂标记
  useEffect(() => {
    if (extraBodyValid) return undefined;
    onExtraBodyInvalidChange(cardKey, true);
    return () => onExtraBodyInvalidChange(cardKey, false);
  }, [extraBodyValid, cardKey, onExtraBodyInvalidChange]);

  function toggleCapability(capability, checked) {
    const current = Array.isArray(profile.capabilities)
      ? profile.capabilities
      : [];
    const next = checked
      ? current.includes(capability)
        ? current
        : [...current, capability]
      : current.filter((item) => item !== capability);
    onPatch({ capabilities: next });
  }

  const referenced =
    referencedRoles.length > 0
      ? {
          label: t("settings.modelReferencedCount", {
            count: referencedRoles.length,
          }),
          title: t("settings.modelReferencedBlockTitle", {
            slots: referencedRoles
              .map((role) => t(`settings.modelRole.${role}`))
              .join("、"),
          }),
        }
      : null;

  const capabilities = Array.isArray(profile.capabilities)
    ? profile.capabilities
    : [];

  return (
    <div className="space-y-3 rounded-md border bg-background p-3">
      {/* 头部：id + 名称 + 引用标记 + 删除 */}
      <div className="flex items-start gap-3">
        <div className="min-w-0 flex-1 space-y-1">
          <Label className="text-xs">{t("settings.modelProfileId")}</Label>
          <Input
            value={profile.id}
            placeholder="vision-pro"
            readOnly={lockedId}
            onChange={(e) => onPatchId(e.target.value)}
            className="font-mono text-xs"
          />
        </div>
        <div className="flex shrink-0 items-center gap-2 pt-5">
          {referenced && (
            <span
              className="rounded bg-amber-500/10 px-1.5 py-0.5 text-xs font-medium text-amber-600 dark:text-amber-400"
              title={referenced.title}
            >
              {referenced.label}
            </span>
          )}
          <Button
            variant="ghost"
            size="icon-xs"
            onClick={onRemove}
            disabled={!!referenced}
            title={
              referenced ? referenced.title : t("settings.modelRemoveProfile")
            }
          >
            <Trash2 className="size-3.5" />
            <span className="sr-only">{t("settings.modelRemoveProfile")}</span>
          </Button>
        </div>
      </div>
      {!lockedId && (
        <p className="text-xs text-muted-foreground">
          {t("settings.modelProfileIdHint")}
        </p>
      )}

      <div className="space-y-1">
        <Label className="text-xs">{t("settings.modelProfileName")}</Label>
        <Input
          value={profile.name}
          placeholder={t("settings.modelProfileNamePlaceholder")}
          onChange={(e) => onPatch({ name: e.target.value })}
          className="text-xs"
        />
      </div>

      <div className="space-y-1">
        <Label className="text-xs">{t("settings.baseUrl")}</Label>
        <Input
          value={profile.base_url}
          placeholder="https://api.example.com/v1"
          onChange={(e) => onPatch({ base_url: e.target.value })}
          className="text-xs"
        />
      </div>

      <div className="grid grid-cols-2 gap-3">
        <div className="space-y-1">
          <Label className="text-xs">{t("settings.apiKey")}</Label>
          <Input
            type="password"
            placeholder="sk-..."
            // 掩码值（"***xxxx" / "****"）原样回填并随保存回传：后端按
            // "***" 前缀 + 档案 id 保留原 Key。短 Key（≤4 位）的掩码是
            // "****"，若像旧实现一样从输入框剔除，用户未触碰字段也会以
            // 空串回传而被后端透传清空（档案 Key 无顶层 llm.api_key 的
            // 空值保护）；清空输入框再保存 = 显式移除
            value={profile.api_key || ""}
            onChange={(e) => onPatch({ api_key: e.target.value })}
            className="text-xs"
          />
          <p className="text-xs text-muted-foreground">
            {t("settings.modelApiKeyHint")}
          </p>
        </div>
        <div className="space-y-1">
          <Label className="text-xs">{t("settings.model")}</Label>
          <Input
            value={profile.model}
            placeholder="glm-4.6v"
            onChange={(e) => onPatch({ model: e.target.value })}
            className="text-xs"
          />
        </div>
      </div>

      <div className="space-y-1.5">
        <Label className="text-xs">{t("settings.modelCapabilities")}</Label>
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5">
          {LLM_CAPABILITIES.map((capability) => (
            <CheckboxField
              key={capability}
              label={t(`settings.modelCapability.${capability}`)}
              checked={capabilities.includes(capability)}
              onChange={(checked) => toggleCapability(capability, checked)}
            />
          ))}
        </div>
      </div>

      {/* 可选覆盖项折叠区（默认继承全局） */}
      <div>
        <button
          type="button"
          onClick={() => setShowOverrides((prev) => !prev)}
          className="flex cursor-pointer items-center gap-1 text-xs text-muted-foreground transition-colors hover:text-foreground"
        >
          {showOverrides ? (
            <ChevronDown className="size-3.5" />
          ) : (
            <ChevronRight className="size-3.5" />
          )}
          {t("settings.modelOverrides")}
          {hasActiveOverride(profile) && (
            <span className="rounded bg-primary/10 px-1 py-0.5 text-primary">
              {t("settings.modelOverridesActive")}
            </span>
          )}
        </button>
        {showOverrides && (
          <div className="mt-2 space-y-3 rounded-md border border-dashed p-3">
            <p className="text-xs text-muted-foreground">
              {t("settings.modelOverridesHint")}
            </p>
            <div className="grid grid-cols-2 gap-3">
              <OverrideNumberField
                label={t("settings.temperature")}
                value={profile.temperature}
                onCommit={(v) => onPatch({ temperature: v })}
                min="0"
                max="2"
                step="0.1"
              />
              <OverrideNumberField
                label={t("settings.maxTokens")}
                value={profile.max_tokens}
                onCommit={(v) => onPatch({ max_tokens: v })}
                min="1"
              />
              <OverrideNumberField
                label={t("settings.requestInterval")}
                // 后端契约：0 / null 均继承顶层（llm_client._resolve_cfg），
                // 与 context_window_tokens 同口径归零显示，避免"显示 0 的
                // 覆盖项实际继承全局"的误导
                value={
                  profile.request_interval > 0 ? profile.request_interval : null
                }
                onCommit={(v) => onPatch({ request_interval: v })}
                min="0"
                step="0.5"
              />
              <OverrideNumberField
                label={t("settings.maxConcurrency")}
                value={profile.max_concurrency}
                onCommit={(v) => onPatch({ max_concurrency: v })}
                min="1"
              />
              <OverrideNumberField
                label={t("settings.llmContextWindow")}
                value={
                  profile.context_window_tokens > 0
                    ? profile.context_window_tokens
                    : null
                }
                onCommit={(v) => onPatch({ context_window_tokens: v })}
                min="0"
              />
              <div className="space-y-1">
                <Label className="text-xs">
                  {t("settings.llmModelsProbeUrl")}
                </Label>
                <Input
                  value={profile.models_probe_url || ""}
                  placeholder="http://host:8000/v1"
                  onChange={(e) =>
                    onPatch({ models_probe_url: e.target.value })
                  }
                  className="text-xs"
                />
              </div>
            </div>
            <div className="space-y-1">
              <Label className="text-xs">{t("settings.llmExtraBody")}</Label>
              <textarea
                className={`h-16 w-full rounded-md border bg-transparent px-3 py-2 font-mono text-xs ${
                  extraBodyValid
                    ? "border-input"
                    : "border-destructive focus-visible:ring-destructive"
                }`}
                placeholder='{"reasoning_effort": "low"}'
                spellCheck={false}
                value={extraBodyText}
                onChange={(e) => handleExtraBodyChange(e.target.value)}
              />
              {!extraBodyValid && (
                <p className="text-xs text-destructive">
                  {t("settings.llmExtraBodyInvalid")}
                </p>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

/**
 * 模型管理设置分区（multi-model-plan §7.1）：模型档案增删改 + 槽位指派。
 *
 * 保存语义：profiles 为整表替换（后端 _deep_merge 列表语义）、assignments
 * 整对象提交，均随 SettingsDialog 的整体保存生效；校验失败错误经
 * request() 翻译层在保存 toast 中展示。
 */
function ModelManagementSection({ llm, onChange, onExtraBodyValidityChange }) {
  const { t } = useTranslation("components");
  const profiles = Array.isArray(llm.profiles) ? llm.profiles : [];
  const assignments = llm.assignments || {};

  // extra_body 草稿非法的卡片键集合：非空 = 至少一张卡片的 JSON 非法，
  // 聚合结果上报对话框禁用保存按钮
  const [invalidExtraBodyKeys, setInvalidExtraBodyKeys] = useState(
    () => new Set(),
  );

  const handleExtraBodyInvalidChange = useCallback((key, invalid) => {
    setInvalidExtraBodyKeys((prev) => {
      const existed = prev.has(key);
      if (invalid === existed) return prev;
      const next = new Set(prev);
      if (invalid) {
        next.add(key);
      } else {
        next.delete(key);
      }
      return next;
    });
  }, []);

  useEffect(() => {
    onExtraBodyValidityChange?.(invalidExtraBodyKeys.size === 0);
  }, [invalidExtraBodyKeys, onExtraBodyValidityChange]);

  // 服务器已保存档案 id 集合（挂载时快照）：已保存档案 id 只读，
  // 防止掩码 api_key 因改 id 无法匹配原值而被后端清空
  const savedIdsRef = useRef(null);
  if (savedIdsRef.current === null) {
    savedIdsRef.current = new Set(profiles.map((profile) => profile.id));
  }

  function setProfiles(next) {
    onChange("llm.profiles", next);
  }

  function setAssignments(next) {
    onChange("llm.assignments", next);
  }

  function addProfile() {
    draftCardKeySeq += 1;
    setProfiles([
      ...profiles,
      {
        [DRAFT_CARD_KEY]: `draft-${draftCardKeySeq}`,
        id: "",
        name: "",
        base_url: "",
        api_key: "",
        model: "",
        capabilities: [],
        temperature: null,
        max_tokens: null,
        request_interval: null,
        max_concurrency: null,
        context_window_tokens: null,
        models_probe_url: "",
        extra_body: {},
      },
    ]);
  }

  function updateProfile(index, patch) {
    setProfiles(
      profiles.map((profile, i) =>
        i === index ? { ...profile, ...patch } : profile,
      ),
    );
  }

  /** 修改档案 id 时同步改写指派表中对旧 id 的引用，避免保存期「引用不存在」被拒 */
  function updateProfileId(index, nextId) {
    const oldId = profiles[index]?.id || "";
    updateProfile(index, { id: nextId });
    if (!oldId || oldId === nextId) return;
    const touched = Object.entries(assignments).some(
      ([, target]) => target === oldId,
    );
    if (!touched) return;
    const next = { ...assignments };
    for (const [role, target] of Object.entries(next)) {
      if (target === oldId) next[role] = nextId;
    }
    setAssignments(next);
  }

  function removeProfile(index) {
    setProfiles(profiles.filter((_, i) => i !== index));
  }

  /** 空指派值 → 下拉哨兵；档案 id → 原值 */
  function toSelectValue(role) {
    const current = assignments[role] || "";
    if (current) return current;
    return role === "default" ? EMPTY_BUILTIN : EMPTY_FOLLOW;
  }

  function handleAssign(role, value) {
    const stored =
      value === EMPTY_BUILTIN || value === EMPTY_FOLLOW ? "" : value;
    setAssignments({ ...assignments, [role]: stored });
  }

  return (
    <ConfigSection title={t("settings.modelManagement")}>
      <p className="text-xs text-muted-foreground">
        {t("settings.modelManagementHint")}
      </p>

      {/* 档案列表（卡片式增删改） */}
      <div className="space-y-2">
        <div className="flex items-center justify-between">
          <Label className="text-xs">{t("settings.modelProfiles")}</Label>
          <Button variant="outline" size="sm" onClick={addProfile}>
            <Plus />
            {t("settings.modelAddProfile")}
          </Button>
        </div>
        {profiles.length === 0 && (
          <p className="text-xs text-muted-foreground">
            {t("settings.modelProfilesEmpty")}
          </p>
        )}
        {profiles.map((profile, index) => (
          <ProfileCard
            // 键与 id 输入解耦：已保存卡片按档案 id（只读、不变），草稿
            // 卡片按创建时分配的稳定键——按 id 草稿卡片每敲一个字符就会
            // 因键变化而重挂载，输入焦点与卡片内草稿态全部丢失
            key={profile[DRAFT_CARD_KEY] || profile.id}
            profile={profile}
            cardKey={profile[DRAFT_CARD_KEY] || profile.id}
            savedIds={savedIdsRef.current}
            referencedRoles={collectSlotReferences(assignments, profile.id)}
            onPatch={(patch) => updateProfile(index, patch)}
            onPatchId={(nextId) => updateProfileId(index, nextId)}
            onRemove={() => removeProfile(index)}
            onExtraBodyInvalidChange={handleExtraBodyInvalidChange}
          />
        ))}
      </div>

      {/* 槽位指派：9 个角色各一个下拉，选项按能力过滤 */}
      <div className="space-y-2 pt-1">
        <div className="flex items-baseline justify-between gap-2">
          <Label className="text-xs">{t("settings.modelAssignments")}</Label>
          <span className="text-right text-xs text-muted-foreground">
            {t("settings.modelAssignmentsHint")}
          </span>
        </div>
        <div className="grid gap-x-3 gap-y-2 sm:grid-cols-2">
          {LLM_ROLE_KEYS.map((role) => {
            const options = buildSlotOptions(profiles, assignments, role);
            return (
              <div key={role} className="space-y-1">
                <Label className="text-xs text-muted-foreground">
                  {t(`settings.modelRole.${role}`)}
                </Label>
                <Select
                  value={toSelectValue(role)}
                  onValueChange={(value) => handleAssign(role, value)}
                >
                  <SelectTrigger size="sm" className="w-full">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {options.map((option) => (
                      <SelectItem
                        key={
                          option.kind === "profile" ? option.value : option.kind
                        }
                        value={
                          option.value === ""
                            ? role === "default"
                              ? EMPTY_BUILTIN
                              : EMPTY_FOLLOW
                            : option.value
                        }
                      >
                        {option.kind === "builtin"
                          ? t("settings.modelSlotBuiltin")
                          : option.kind === "followDefault"
                            ? t("settings.modelSlotFollowDefault")
                            : formatProfileOption(option.profile)}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            );
          })}
        </div>
      </div>
    </ConfigSection>
  );
}

export default memo(ModelManagementSection);
