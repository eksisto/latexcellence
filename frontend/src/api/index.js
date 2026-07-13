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

  /* ---- 配置管理 ---- */
  config: {
    get: () => request("/config"),
    update: (data) =>
      request("/config", {
        method: "POST",
        body: JSON.stringify(data),
      }),
  },

  /* ---- 项目管理 ---- */
  project: {
    open: (path) =>
      request("/project/open", {
        method: "POST",
        body: JSON.stringify({ path }),
      }),
    lastOpened: () => request("/project/last-opened"),
    fileTree: () => request("/project/file-tree"),
    paragraphs: (filePath) =>
      request(
        `/project/paragraphs?file_path=${encodeURIComponent(filePath)}`
      ),
    applyChange: (filePath, paragraphIndex, newText) =>
      request("/project/apply-change", {
        method: "POST",
        body: JSON.stringify({
          file_path: filePath,
          paragraph_index: paragraphIndex,
          new_text: newText,
        }),
      }),
    undo: (filePath) =>
      request("/project/undo", {
        method: "POST",
        body: JSON.stringify({ file_path: filePath }),
      }),
    redo: (filePath) =>
      request("/project/redo", {
        method: "POST",
        body: JSON.stringify({ file_path: filePath }),
      }),
    setMainFile: (filePath) =>
      request("/project/set-main-file", {
        method: "POST",
        body: JSON.stringify({ file_path: filePath }),
      }),
  },

  /* ---- 文本校对 ---- */
  typoCheck: {
    preview: (filePath) =>
      request(
        `/typo-check/preview?file_path=${encodeURIComponent(filePath)}`
      ),
    check: (data) =>
      request("/typo-check/check", {
        method: "POST",
        body: JSON.stringify(data),
      }),
    apply: (data) =>
      request("/typo-check/apply", {
        method: "POST",
        body: JSON.stringify(data),
      }),
    stream: (projectId, filePath) =>
      `${BASE_URL}/typo-check/stream/${projectId}?file_path=${encodeURIComponent(filePath)}`,
  },

  /* ---- 表达优化 ---- */
  expressionOpt: {
    preview: (filePath) =>
      request(
        `/expression-opt/preview?file_path=${encodeURIComponent(filePath)}`
      ),
    optimize: (data) =>
      request("/expression-opt/optimize", {
        method: "POST",
        body: JSON.stringify(data),
      }),
    apply: (data) =>
      request("/expression-opt/apply", {
        method: "POST",
        body: JSON.stringify(data),
      }),
    stream: (projectId, filePath) =>
      `${BASE_URL}/expression-opt/stream/${projectId}?file_path=${encodeURIComponent(filePath)}`,
    prompts: {
      list: () => request("/expression-opt/prompts"),
      load: (name) => request(`/expression-opt/prompts/${encodeURIComponent(name)}`),
      save: (name, content) =>
        request("/expression-opt/prompts", {
          method: "POST",
          body: JSON.stringify({ name, content }),
        }),
      delete: (name) =>
        request(`/expression-opt/prompts/${encodeURIComponent(name)}`, {
          method: "DELETE",
        }),
      setActive: (name) =>
        request("/expression-opt/prompts/active", {
          method: "POST",
          body: JSON.stringify({ name }),
        }),
    },
  },

  /* ---- 公式识别（图片上传使用 FormData） ---- */
  formulaOCR: {
    recognize: async (formData) => {
      const res = await fetch(`${BASE_URL}/formula-ocr/recognize`, {
        method: "POST",
        body: formData,
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: res.statusText }));
        throw new Error(err.detail || `HTTP ${res.status}`);
      }
      return res.json();
    },
  },

  /* ---- 表格 OCR 识别（图片上传使用 FormData） ---- */
  tableOCR: {
    recognize: async (formData) => {
      const res = await fetch(`${BASE_URL}/table-ocr/recognize`, {
        method: "POST",
        body: formData,
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: res.statusText }));
        throw new Error(err.detail || `HTTP ${res.status}`);
      }
      return res.json();
    },
  },

  /* ---- 模板生成 ---- */
  templateGen: {
    generate: (data) =>
      request("/template-gen/generate", {
        method: "POST",
        body: JSON.stringify(data),
      }),
    save: (data) =>
      request("/template-gen/save", {
        method: "POST",
        body: JSON.stringify(data),
      }),
  },

  /* ---- 源码格式化 ---- */
  formatting: {
    preview: (filePath) =>
      request(
        `/formatting/preview?file_path=${encodeURIComponent(filePath)}`
      ),
    check: (data) =>
      request("/formatting/check", {
        method: "POST",
        body: JSON.stringify(data),
      }),
    apply: (data) =>
      request("/formatting/apply", {
        method: "POST",
        body: JSON.stringify(data),
      }),
    stream: (projectId, filePath) =>
      `${BASE_URL}/formatting/stream/${projectId}?file_path=${encodeURIComponent(filePath)}`,
  },

  /* ---- 自动编译 ---- */
  compile: {
    run: (mainFile) =>
      request("/compile/run", {
        method: "POST",
        body: JSON.stringify({ main_file: mainFile }),
      }),
  },
};
