/**
 * 多用户模式 SMTP 邮件能力（email_available）。
 *
 * 返回值：true / false；null 表示未知（单用户模式、请求中或探测失败）。
 * 调用方约定：仅在明确 false 时隐藏「忘记密码」入口，未知时不隐藏
 * （宁可多展示，后端 forgot-password 仍会以统一降级响应兜底）。
 * 能力透出复用既有公开端点 /api/config/status（最小侵入：登录页已在
 * useRegistrationEnabled 中探测同一端点，不新增探测面）。
 */
import { useEffect, useState } from "react";
import { api } from "@/api/index";
import { isMultiUser } from "@/api/connection";

export default function useEmailAvailable() {
  const [available, setAvailable] = useState(null);

  useEffect(() => {
    if (!isMultiUser()) return;
    let mounted = true;
    api
      .configStatus()
      .then((data) => {
        if (mounted) setAvailable(Boolean(data.email_available));
      })
      .catch(() => {
        // 探测失败保持 null：入口不隐藏，提交时后端降级响应兜底
      });
    return () => {
      mounted = false;
    };
  }, []);

  return available;
}
