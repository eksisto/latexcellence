/**
 * 多用户模式是否开放注册（registration_enabled）。
 *
 * 返回值：true / false；null 表示未知（单用户模式、请求中或探测失败）。
 * 调用方约定：仅在明确 false 时隐藏注册入口，未知时不隐藏（宁可多展示，
 * 后端注册接口仍会以 registration_disabled 拒绝兜底）。
 * 探测经 useConfigStatus 共享层去重，避免与 useEmailAvailable 等同时
 * 挂载时重复请求 /api/config/status。
 */
import useConfigStatus from "@/hooks/useConfigStatus";

export default function useRegistrationEnabled() {
  const status = useConfigStatus();
  return status == null ? null : Boolean(status.registration_enabled);
}
