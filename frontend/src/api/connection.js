/**
 * 后端连接配置与连接测试。
 *
 * 职责：
 * - 管理 API 基地址 / 访问令牌的运行时配置
 *   （优先级：设置页 localStorage → VITE_API_BASE_URL → 默认同源 /api）
 * - 统一构造认证请求头，并为无法自定义请求头的 URL（EventSource 等）追加令牌
 * - 提供连接测试（health 验证可达性，config/status 验证令牌是否有效）
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

// localStorage 读取是同步 I/O，请求路径上会高频调用（authHeaders / resolveUrl / appendToken），
// 用内存缓存减少重复读取；setApiConnection 同步更新缓存，其他标签页改动由 storage 事件失效。
let cachedApiBase = DEFAULT_API_BASE;
let apiBaseLoaded = false;
let cachedApiToken = null;

function invalidateConnectionCache() {
  apiBaseLoaded = false;
  cachedApiToken = null;
}

if (typeof window !== "undefined") {
  window.addEventListener("storage", (event) => {
    if (
      !event.key ||
      event.key === STORAGE_BASE_KEY ||
      event.key === STORAGE_TOKEN_KEY
    ) {
      invalidateConnectionCache();
    }
  });
}

/** 当前后端 API 基地址（设置页写入 localStorage，其次构建时环境变量） */
export function getApiBase() {
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

/** 当前访问令牌（后端未启用令牌校验时为空字符串） */
export function getApiToken() {
  if (cachedApiToken === null) {
    try {
      cachedApiToken = localStorage.getItem(STORAGE_TOKEN_KEY) || "";
    } catch {
      cachedApiToken = "";
    }
  }
  return cachedApiToken;
}

/** 保存后端连接配置（设置页调用，立即生效） */
export function setApiConnection(apiBase, apiToken) {
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

/** 合并访问令牌请求头（未配置令牌时保持原有 headers） */
export function authHeaders(extra = {}) {
  const headers = { ...extra };
  const token = getApiToken();
  if (token) headers[TOKEN_HEADER] = token;
  return headers;
}

/** 为无法自定义请求头的 URL（EventSource 等）追加令牌查询参数 */
export function appendToken(url) {
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
  const base = (apiBase || getApiBase()).replace(/\/+$/, "");
  const headers = { "Content-Type": "application/json" };
  if (apiToken) headers[TOKEN_HEADER] = apiToken;
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
