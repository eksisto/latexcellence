/**
 * 多用户模式认证引导（P5）。
 *
 * 启动时一次性执行：
 *   1. 确定后端模式与基地址：探测顺序为显式绑定地址 → 同源 /api/config/status
 *      （生产同源 / dev 走 Vite 代理）→ 配置的默认基地址（兼容独立部署前端）
 *   2. 单用户：authStatus → "single"（路由守卫放行，行为保持现状）
 *   3. 多用户：经 /api/auth/me 恢复当前会话（Cookie）→ authenticated / pending / unauthenticated
 *
 * authStatus 初始为 "loading"，路由守卫据此显示加载态；
 * /api/auth/me 返回 401 时也会触发 api/index.js 的全局 401 回调（handleUnauthorized）。
 */

import { useEffect } from "react";
import { api } from "@/api/index";
import {
  authHeaders,
  getDefaultApiBase,
  getStoredApiBase,
  setMultiUserMode,
} from "@/api/connection";
import useStore from "@/store/useStore";

// 每个探测地址的超时（毫秒）：绑定不可达地址时避免启动卡在全屏 loading
// 等待浏览器默认网络超时（可达数十秒）；超时视为该地址不可达，继续优先级探测。
const PROBE_TIMEOUT_MS = 4000;

/**
 * 探测一个基地址的 /config/status；不可达 / 超时 / 非 2xx 返回 null。
 * @returns {Promise<{multiUser: boolean, base: string} | null>}
 */
async function probeStatus(base) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), PROBE_TIMEOUT_MS);
  try {
    const res = await fetch(`${base}/config/status`, {
      headers: authHeaders({ "Content-Type": "application/json" }),
      signal: controller.signal,
    });
    if (!res.ok) return null;
    const data = await res.json();
    return { multiUser: data.multi_user === true, base };
  } catch {
    return null;
  } finally {
    clearTimeout(timer);
  }
}

/**
 * 确定后端模式与基地址。探测顺序（优先级）：
 *   1. 用户显式绑定的基地址（认证页/设置页连接面板保存，改绑其他后端的出口）
 *   2. 同源 /api/config/status（生产同源 / dev 走 Vite 代理）
 *   3. 配置的默认基地址（VITE_API_BASE_URL / 内置默认，兼容独立部署前端绑定远程后端）
 * 显式地址不可达时继续自动探测，避免误锁死在不可用地址。
 *
 * 三个探测相互独立，并行发出（async-parallel），全部完成后按上述优先级裁决，
 * 避免串行探测时"显式地址卡超时 → 同源探测被拖后"的启动延迟。
 */
async function detectMultiUserMode() {
  const candidateBases = [
    getStoredApiBase(),
    "/api",
    getDefaultApiBase(),
  ].filter(Boolean);

  const results = await Promise.all(
    [...new Set(candidateBases)].map(async (base) => ({
      base,
      hit: await probeStatus(base),
    })),
  );

  // 按候选顺序取第一个命中
  const order = new Map(
    [...new Set(candidateBases)].map((base, i) => [base, i]),
  );
  const winner = results
    .filter((r) => r.hit)
    .sort((a, b) => order.get(a.base) - order.get(b.base))[0];

  // 全部不可达：按单用户模式放行（本地开发后端未启动等场景行为保持现状）
  return winner ? winner.hit : { multiUser: false, base: null };
}

export default function useAuthBootstrap() {
  const setAuthStatus = useStore((s) => s.setAuthStatus);
  const setAuthMe = useStore((s) => s.setAuthMe);

  useEffect(() => {
    (async () => {
      const { multiUser, base } = await detectMultiUserMode();
      setMultiUserMode(multiUser, base);
      // 模式可能被探测修正（首访无持久化标志时 base 曾指向默认地址）：
      // 重置功能开关，让 useEnabledFeatures 以正确基地址重新拉取
      useStore.setState({ enabledFeatures: null });

      if (!multiUser) {
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
