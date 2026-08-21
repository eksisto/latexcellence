/**
 * 多用户模式认证引导（P5）。
 *
 * 启动时一次性执行：
 *   1. 确定后端模式：优先同源 /api/config/status（生产同源 / dev 走 Vite 代理），
 *      失败时回退按配置的 API 基地址探测（兼容单用户远程部署）
 *   2. 单用户：authStatus → "single"（路由守卫放行，行为保持现状）
 *   3. 多用户：经 /api/auth/me 恢复当前会话（Cookie）→ authenticated / pending / unauthenticated
 *
 * authStatus 初始为 "loading"，路由守卫据此显示加载态；
 * /api/auth/me 返回 401 时也会触发 api/index.js 的全局 401 回调（handleUnauthorized）。
 */

import { useEffect } from "react";
import { api } from "@/api/index";
import { authHeaders, setMultiUserMode } from "@/api/connection";
import useStore from "@/store/useStore";

/** 探测后端是否处于多用户模式：先同源，再按配置基地址回退 */
async function detectMultiUserMode() {
  try {
    const res = await fetch("/api/config/status", {
      headers: authHeaders({ "Content-Type": "application/json" }),
    });
    if (res.ok) {
      const data = await res.json();
      return data.multi_user === true;
    }
  } catch {
    // 同源不可达（如自定义远程基地址的单用户部署），走配置基地址
  }
  try {
    const status = await api.configStatus();
    return status?.multi_user === true;
  } catch {
    // 完全不可达：不阻塞页面，按单用户处理（后续请求由后端 401/403 兜底）
    return false;
  }
}

export default function useAuthBootstrap() {
  const setAuthStatus = useStore((s) => s.setAuthStatus);
  const setAuthMe = useStore((s) => s.setAuthMe);

  useEffect(() => {
    (async () => {
      const multi = await detectMultiUserMode();
      setMultiUserMode(multi);
      // 模式可能被探测修正（首访无持久化标志时 base 曾指向默认地址）：
      // 重置功能开关，让 useEnabledFeatures 以正确基地址重新拉取
      useStore.setState({ enabledFeatures: null });

      if (!multi) {
        setAuthStatus("single");
        return;
      }
      try {
        const me = await api.auth.me();
        if (me?.user?.status === "pending") {
          setAuthStatus("pending");
        } else {
          setAuthMe(me);
          setAuthStatus("authenticated");
        }
      } catch {
        // 401（未登录/会话过期）→ unauthenticated；其他异常同样视为未登录
        setAuthStatus("unauthenticated");
      }
    })();
  }, [setAuthStatus, setAuthMe]);
}

/** 登出：吊销会话并清空本地认证/项目状态（多用户模式） */
export async function logoutUser() {
  const useStore = (await import("@/store/useStore")).default;
  try {
    const { api } = await import("@/api/index");
    await api.auth.logout();
  } catch {
    // 后端不可达时仍清空本地状态
  }
  useStore.getState().clearAuth();
  useStore.getState().clearProject();
}
