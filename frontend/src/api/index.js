/**
 * API 层 —— 封装所有后端接口调用
 *
 * 设计说明：
 * - BASE_URL 固定为 "/api"，开发时由 Vite 代理到后端，生产时同域访问
 * - request() 自动处理 JSON 序列化/反序列化和错误提取
 * - formulaOCR 模块使用 FormData（图片上传），不走 JSON 请求
 * - typoCheck 的 stream() 返回 SSE URL 字符串，供 EventSource 消费
 */

const BASE_URL = "/api";

/**
 * 通用请求函数
 * @param {string} url     API 路径（如 "/health"）
 * @param {object} options fetch 选项（method, body, headers 等）
 * @returns {Promise<object>} 解析后的 JSON 响应
 */
async function request(url, options = {}) {
  const res = await fetch(`${BASE_URL}${url}`, {
    headers: { "Content-Type": "application/json", ...options.headers },
    ...options,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || `HTTP ${res.status}`);
  }
  return res.json();
}

export const api = {
  /* ---- 系统 ---- */
  health: () => request("/health"),
  configStatus: () => request("/config/status"),

  /* ---- 项目管理 ---- */
  project: {
    open: (path) =>
      request("/project/open", {
        method: "POST",
        body: JSON.stringify({ path }),
      }),
    fileTree: () => request("/project/file-tree"),
    paragraphs: (filePath) =>
      request(
        `/project/paragraphs?file_path=${encodeURIComponent(filePath)}`
      ),
  },

  /* ---- 错字查找 ---- */
  typoCheck: {
    check: (data) =>
      request("/typo-check/check", {
        method: "POST",
        body: JSON.stringify(data),
      }),
    apply: (changes) =>
      request("/typo-check/apply", {
        method: "POST",
        body: JSON.stringify(changes),
      }),
    stream: (projectId) => `${BASE_URL}/typo-check/stream/${projectId}`,
  },

  /* ---- 表达优化 ---- */
  expressionOpt: {
    optimize: (data) =>
      request("/expression-opt/optimize", {
        method: "POST",
        body: JSON.stringify(data),
      }),
    apply: (changes) =>
      request("/expression-opt/apply", {
        method: "POST",
        body: JSON.stringify(changes),
      }),
  },

  /* ---- 公式识别（图片上传使用 FormData） ---- */
  formulaOCR: {
    recognize: (formData) =>
      fetch(`${BASE_URL}/formula-ocr/recognize`, {
        method: "POST",
        body: formData,
      }).then((r) => r.json()),
  },

  /* ---- 模板生成 ---- */
  templateGen: {
    generate: (data) =>
      request("/template-gen/generate", {
        method: "POST",
        body: JSON.stringify(data),
      }),
  },
};
