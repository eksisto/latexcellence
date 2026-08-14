/**
 * API 地址与令牌 URL 纯函数工具。
 *
 * 不依赖 Vite / i18n / localStorage / window，便于 node --test 直接单测；
 * api/index.js 只负责从运行时环境取参后调用这些纯函数。
 */

/** 去除 API 基地址末尾斜杠；空值回退到默认基地址 */
export function normalizeApiBase(value, fallback = "/api") {
  return (value || fallback).replace(/\/+$/, "");
}

/** 为无法自定义请求头的 URL（EventSource 等）追加令牌查询参数 */
export function appendTokenToUrl(url, token) {
  if (!token) return url;
  return `${url}${url.includes("?") ? "&" : "?"}token=${encodeURIComponent(token)}`;
}

/**
 * 将后端返回的相对路径（如 /api/git-diff/pdf/xxx）解析为完整地址。
 *
 * 不追加访问令牌：fetch 场景通过请求头认证，避免令牌进入 URL/访问日志；
 * 第三方绝对 URL 原样返回，避免令牌泄漏给外部站点。
 *
 * @param {string} path       后端返回的相对/绝对地址
 * @param {string} apiBase    当前 API 基地址（相对 /api 或绝对地址）
 * @param {string} pageOrigin 页面 origin（相对 base 时用于补全，如 window.location.origin）
 */
export function resolveUrlPath(path, apiBase, pageOrigin) {
  if (!path) return path;
  const base = normalizeApiBase(apiBase);
  try {
    const absoluteBase = /^https?:\/\//i.test(base)
      ? `${base}/`
      : `${pageOrigin}${base}/`;
    return new URL(path, absoluteBase).toString();
  } catch {
    // base 配置异常时退回拼接行为，避免抛错中断页面
    return `${base}${path.startsWith("/") ? path : `/${path}`}`;
  }
}
