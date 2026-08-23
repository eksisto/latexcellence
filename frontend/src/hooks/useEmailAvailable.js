/**
 * 多用户模式 SMTP 邮件能力（email_available）。
 *
 * 返回值：true / false；null 表示未知（单用户模式、请求中或探测失败）。
 * 调用方约定：仅在明确 false 时隐藏「忘记密码」入口，未知时不隐藏
 * （宁可多展示，后端 forgot-password 仍会以统一降级响应兜底）。
 * 能力透出复用既有公开端点 /api/config/status；探测经 useConfigStatus
 * 共享层去重——登录页同时挂载 useRegistrationEnabled 与本 hook 时
 * 只发一次请求，不新增探测面。
 */
import useConfigStatus from "@/hooks/useConfigStatus";

export default function useEmailAvailable() {
  const status = useConfigStatus();
  return status == null ? null : Boolean(status.email_available);
}
