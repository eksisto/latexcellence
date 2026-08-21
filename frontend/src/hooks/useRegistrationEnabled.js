/**
 * 多用户模式是否开放注册（registration_enabled）。
 *
 * 返回值：true / false；null 表示未知（单用户模式、请求中或探测失败）。
 * 调用方约定：仅在明确 false 时隐藏注册入口，未知时不隐藏（宁可多展示，
 * 后端注册接口仍会以 registration_disabled 拒绝兜底）。
 */
import { useEffect, useState } from "react";
import { api } from "@/api/index";
import { isMultiUser } from "@/api/connection";

export default function useRegistrationEnabled() {
  const [enabled, setEnabled] = useState(null);

  useEffect(() => {
    if (!isMultiUser()) return;
    let mounted = true;
    api
      .configStatus()
      .then((data) => {
        if (mounted) setEnabled(Boolean(data.registration_enabled));
      })
      .catch(() => {
        // 探测失败保持 null：入口不隐藏，提交时后端 400 兜底
      });
    return () => {
      mounted = false;
    };
  }, []);

  return enabled;
}
