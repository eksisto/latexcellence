/**
 * 后端连接配置与连接测试。
 *
 * 职责：
 * - 管理 API 基地址 / 访问令牌的运行时配置
 *   （优先级：设置页 localStorage → VITE_API_BASE_URL → 默认 http://localhost:8000/api）
 * - 统一构造认证请求头，并为无法自定义请求头的 URL（EventSource 等）追加令牌
 * - 提供连接测试（health 验证可达性，config/status 验证令牌是否有效）
 * - 多用户模式（P5）：API 基地址固定为同源 /api（生产由 FastAPI 挂载 static/、
 *   dev 由 Vite 代理转发），认证改为 HttpOnly 会话 Cookie + 双提交 CSRF 头，
 *   SSE 同源自动携带 Cookie，不再追加 token 查询参数
 *
 * 纯 URL 计算逻辑（normalizeApiBase / appendTokenToUrl / resolveUrlPath）
 * 在 utils/apiUrl.js 中，本模块只负责与运行时环境（localStorage / fetch）交互。
 */

import { translateError } from "@/utils/errorMessages";
import {
  appendTokenToUrl,
  normalizeApiBase,
  resolveUrlPath,
} from "@/utils/apiUrl";

const DEFAULT_API_BASE = normalizeApiBase(import.meta.env.VITE_API_BASE_URL);
const STORAGE_BASE_KEY = "latexcellence_api_base";
const STORAGE_TOKEN_KEY = "latexcellence_api_token";
const TOKEN_HEADER = "X-Latexcellence-Token";
// 多用户模式：双提交 CSRF——请求头值须与 CSRF Cookie（latexcellence_csrf）一致
const MULTIUSER_MODE_KEY = "latexcellence_multiuser";
const CSRF_COOKIE_NAME = "latexcellence_csrf";
const CSRF_HEADER = "X-Latexcellence-CSRF";

// localStorage 读取是同步 I/O，请求路径上会高频调用（authHeaders / resolveUrl / appendToken），
// 用内存缓存减少重复读取；setApiConnection 同步更新缓存，其他标签页改动由 storage 事件失效。
let cachedApiBase = DEFAULT_API_BASE;
let apiBaseLoaded = false;
let cachedApiToken = null;
let multiUserMode = null; // null=尚未确定（默认按单用户路径缓存 base/token）

/** 多用户模式下可选的 401 全局回调（App 挂载时注册 → 登出/跳转登录页） */
let unauthorizedHandler = null;

function invalidateConnectionCache() {
  apiBaseLoaded = false;
  cachedApiToken = null;
  multiUserMode = null;
}

if (typeof window !== "undefined") {
  window.addEventListener("storage", (event) => {
    if (
      !event.key ||
      event.key === STORAGE_BASE_KEY ||
      event.key === STORAGE_TOKEN_KEY ||
      event.key === MULTIUSER_MODE_KEY
    ) {
      invalidateConnectionCache();
    }
  });
}

/** 多用户模式是否已启用（localStorage 缓存 + 内存标志） */
export function isMultiUser() {
  if (multiUserMode === null) {
    try {
      multiUserMode = localStorage.getItem(MULTIUSER_MODE_KEY) === "1";
    } catch (_) {
      multiUserMode = false;
    }
  }
  return multiUserMode;
}

/**
 * 设置多用户模式标志（应用启动时根据 /api/config/status 的 multi_user 调用）。
 * 持久化到 localStorage，让刷新后的首帧请求立即使用正确的同源基地址与认证路径。
 */
export function setMultiUserMode(enabled) {
  multiUserMode = !!enabled;
  try {
    if (enabled) localStorage.setItem(MULTIUSER_MODE_KEY, "1");
    else localStorage.removeItem(MULTIUSER_MODE_KEY);
  } catch (_) {
    // 隐私模式等场景忽略
  }
  invalidateConnectionCache();
}

/** 注册 401 全局回调（会话过期/未登录时由 App 登出并跳转登录页） */
export function setUnauthorizedHandler(handler) {
  unauthorizedHandler = handler;
}

/** 通知全局 401（供 api/index.js 请求层调用，避免 api 层依赖 store） */
export function notifyUnauthorized() {
  if (typeof unauthorizedHandler === "function") {
    try {
      unauthorizedHandler();
    } catch (_) {
      // 忽略
    }
  }
}

function multiuserApiBase() {
  // 同源固定路径：生产由 FastAPI 挂载 static/ 同源提供；dev 由 Vite 代理 /api → 8000
  return "/api";
}

/** 当前后端 API 基地址（多用户模式固定同源 /api） */
export function getApiBase() {
  if (isMultiUser()) return multiuserApiBase();
  if (!apiBaseLoaded) {
    try {
      cachedApiBase = normalizeApiBase(
        localStorage.getItem(STORAGE_BASE_KEY),
        DEFAULT_API_BASE,
      );
    } catch {
      cachedApiBase = DEFAULT_API_BASE;
    }
    apiBaseLoaded = true;
  }
  return cachedApiBase;
}

/** 当前访问令牌（多用户模式返回空串：认证走 Cookie 会话） */
export function getApiToken() {
  if (isMultiUser()) return "";
  if (cachedApiToken === null) {
    try {
      cachedApiToken = localStorage.getItem(STORAGE_TOKEN_KEY) || "";
    } catch {
      cachedApiToken = "";
    }
  }
  return cachedApiToken;
}

/** 读取可被 JS 读取的 CSRF Cookie（双提交模式，仅多用户模式使用） */
export function getCsrfToken() {
  if (typeof document === "undefined") return "";
  try {
    const m = document.cookie.match(
      new RegExp("(?:^|;\\s*)" + CSRF_COOKIE_NAME + "=([^;]*)"),
    );
    return m ? decodeURIComponent(m[1]) : "";
  } catch {
    return "";
  }
}

/** 保存后端连接配置（设置页调用，立即生效；多用户模式调用无效） */
export function setApiConnection(apiBase, apiToken) {
  if (isMultiUser()) return;
  const base = normalizeApiBase(apiBase, DEFAULT_API_BASE);
  const token = apiToken || "";
  try {
    localStorage.setItem(STORAGE_BASE_KEY, base);
    localStorage.setItem(STORAGE_TOKEN_KEY, token);
  } catch {
    // 隐私模式等场景 localStorage 不可用，忽略
  }
  cachedApiBase = base;
  apiBaseLoaded = true;
  cachedApiToken = token;
}

/** 合并认证请求头（多用户模式：附加 CSRF 头；单用户模式：附加访问令牌） */
export function authHeaders(extra = {}) {
  const headers = { ...extra };
  if (isMultiUser()) {
    const csrf = getCsrfToken();
    if (csrf) headers[CSRF_HEADER] = csrf;
    return headers;
  }
  const token = getApiToken();
  if (token) headers[TOKEN_HEADER] = token;
  return headers;
}

/** 为无法自定义请求头的 URL（EventSource 等）构造地址（多用户模式不追加 token） */
export function appendToken(url) {
  if (isMultiUser()) return url;
  return appendTokenToUrl(url, getApiToken());
}

/** 将后端返回的相对路径解析为完整地址（不追加令牌，实现见 utils/apiUrl.js） */
export function resolveUrl(path) {
  return resolveUrlPath(path, getApiBase(), window.location.origin);
}

/** 解析连接测试响应；非 2xx 时抛出翻译后的错误 */
async function parseConnectionResponse(response) {
  if (!response.ok) {
    const err = await response
      .json()
      .catch(() => ({ detail: response.statusText }));
    throw new Error(translateError(err.detail || `HTTP ${response.status}`));
  }
  return response.json();
}

/**
 * 连接测试：health 验证可达性，config/status 验证令牌是否有效。
 *
 * @param {string} [apiBase]  待测试的 API 基地址；缺省时使用当前配置
 * @param {string} [apiToken] 待测试的访问令牌；缺省时使用当前配置
 * @param {AbortSignal} [signal] 中止信号（面板关闭时取消自动探测）
 * @returns {Promise<object>} health 与 config/status 合并后的信息
 */
export async function checkConnection(apiBase, apiToken, signal) {
  const base = isMultiUser()
    ? multiuserApiBase()
    : (apiBase || getApiBase()).replace(/\/+$/, "");
  const headers = { "Content-Type": "application/json" };
  if (isMultiUser()) {
    const csrf = getCsrfToken();
    if (csrf) headers[CSRF_HEADER] = csrf;
  } else if (apiToken) {
    headers[TOKEN_HEADER] = apiToken;
  }
  try {
    // health 与 config/status 相互独立，并行发出，成功路径耗时减半
    const [health, status] = await Promise.all([
      fetch(`${base}/health`, { headers, signal }).then(
        parseConnectionResponse,
      ),
      fetch(`${base}/config/status`, { headers, signal }).then(
        parseConnectionResponse,
      ),
    ]);
    return { ...health, ...status };
  } catch (e) {
    if (e.name === "AbortError") throw e;
    // fetch 网络错误（Failed to fetch / Load failed 等）同样需要翻译
    throw new Error(translateError(e.message || "Request timed out"));
  }
}
