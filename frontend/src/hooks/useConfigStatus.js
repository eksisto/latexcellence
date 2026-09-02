/**
 * 多用户模式公开配置探测（/api/config/status）共享层。
 *
 * useRegistrationEnabled / useEmailAvailable / useEnabledFeatures /
 * UploadProjectDialog 的公共底座，解决多个消费方各自独立请求同一端点的
 * 重复探测（client-swr-dedup）：
 * - 模块级 in-flight Promise 去重：并发消费者共享同一次请求；
 * - 短 TTL 结果缓存：页面快速往返的重挂载不再重复请求。
 *
 * 返回值：status 对象；null 表示未知（单用户模式、请求中或探测失败）。
 * 调用方约定与派生 hook 一致：仅在明确 false 时隐藏入口，未知时不隐藏
 * （宁可多展示，由对应后端接口兜底拒绝）。配置项变更频率极低，短缓存安全。
 */
import { useEffect, useState } from "react";
import { api } from "@/api/index";
import { isMultiUser } from "@/api/connection";

const CACHE_TTL_MS = 30_000;

let cachedStatus = null;
let cachedAt = 0;
let inflight = null;

export function fetchConfigStatus() {
  if (cachedStatus && Date.now() - cachedAt < CACHE_TTL_MS) {
    return Promise.resolve(cachedStatus);
  }
  if (!inflight) {
    inflight = api
      .configStatus()
      .then((data) => {
        cachedStatus = data || {};
        cachedAt = Date.now();
        return cachedStatus;
      })
      .finally(() => {
        inflight = null;
      });
  }
  return inflight;
}

/**
 * 用一次已知的探测结果直接填充缓存（不覆盖 in-flight）：useAuth 启动探测
 * 已经拉到完整 /config/status payload 时复用它，消除启动期的第二次请求。
 * 仅在缓存为空时写入，避免以旧探测结果覆盖更新的响应。
 */
export function seedConfigStatusCache(data) {
  if (!data || cachedStatus || inflight) return;
  cachedStatus = data;
  cachedAt = Date.now();
}

export default function useConfigStatus() {
  const [status, setStatus] = useState(null);

  useEffect(() => {
    if (!isMultiUser()) return undefined;
    let mounted = true;
    fetchConfigStatus()
      .then((data) => {
        if (mounted) setStatus(data);
      })
      .catch(() => {
        // 探测失败保持 null：入口不隐藏，提交时后端兜底
      });
    return () => {
      mounted = false;
    };
  }, []);

  return status;
}
