/**
 * API 层 —— 封装所有后端接口调用
 *
 * 设计说明：
 * - BASE_URL 固定为 "/api"，开发时由 Vite 代理到后端，生产时同域访问
 * - request() 自动处理 JSON 序列化/反序列化和错误提取
 * - 后端英文错误经 translateError() 统一翻译为中文后抛出
 * - formulaOCR 模块使用 FormData（图片上传），不走 JSON 请求
 * - typoCheck 的 stream() 返回 SSE URL 字符串，供 EventSource 消费
 * - 所有需要工作区隔离的端点均携带 workspace_id 查询参数
 */

import { translateError } from "@/utils/errorMessages";

const BASE_URL = "/api";

/**
 * 通用 FormData 请求函数（用于文件上传场景）
 * @param {string} url      API 路径
 * @param {FormData} formData  表单数据
 * @param {string} method   HTTP 方法
 * @returns {Promise<object>} 解析后的 JSON 响应
 */
async function requestForm(url, formData, method = "POST") {
  let res;
  try {
    res = await fetch(`${BASE_URL}${url}`, { method, body: formData });
  } catch (e) {
    throw new Error(translateError(e.message || "Request timed out"));
  }
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(translateError(err.detail || `HTTP ${res.status}`));
  }
  return res.json();
}

/**
 * 拼接 workspace_id 到 URL 查询参数
 * @param {string} url    含现有查询参数的 URL
 * @param {string} workspaceId   工作区标识
 * @returns {string}
 */
function appendWs(url, workspaceId) {
  const sep = url.includes("?") ? "&" : "?";
  return `${url}${sep}workspace_id=${encodeURIComponent(workspaceId)}`;
}

/**
 * 通用请求函数
 * @param {string} url     API 路径（如 "/health"）
 * @param {object} options fetch 选项（method, body, headers 等）
 * @returns {Promise<object>} 解析后的 JSON 响应
 */
async function request(url, options = {}) {
  let res;
  try {
    res = await fetch(`${BASE_URL}${url}`, {
      headers: { "Content-Type": "application/json", ...options.headers },
      ...options,
    });
  } catch (e) {
    throw new Error(translateError(e.message || "Request timed out"));
  }
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(translateError(err.detail || `HTTP ${res.status}`));
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
    open: (path, workspaceId) =>
      request(appendWs("/project/open", workspaceId), {
        method: "POST",
        body: JSON.stringify({ path }),
      }),
    browse: (path = "") =>
      request(`/project/browse?path=${encodeURIComponent(path)}`),
    fileTree: (workspaceId) =>
      request(appendWs("/project/file-tree", workspaceId)),
    paragraphs: (filePath, workspaceId) =>
      request(
        appendWs(
          `/project/paragraphs?file_path=${encodeURIComponent(filePath)}`,
          workspaceId
        )
      ),
    applyChange: (filePath, paragraphIndex, newText, workspaceId) =>
      request(appendWs("/project/apply-change", workspaceId), {
        method: "POST",
        body: JSON.stringify({
          file_path: filePath,
          paragraph_index: paragraphIndex,
          new_text: newText,
        }),
      }),
    undo: (filePath, workspaceId) =>
      request(appendWs("/project/undo", workspaceId), {
        method: "POST",
        body: JSON.stringify({ file_path: filePath }),
      }),
    redo: (filePath, workspaceId) =>
      request(appendWs("/project/redo", workspaceId), {
        method: "POST",
        body: JSON.stringify({ file_path: filePath }),
      }),
    canUndo: (filePath, workspaceId) =>
      request(
        appendWs(
          `/project/can-undo?file_path=${encodeURIComponent(filePath)}`,
          workspaceId
        )
      ),
    canRedo: (filePath, workspaceId) =>
      request(
        appendWs(
          `/project/can-redo?file_path=${encodeURIComponent(filePath)}`,
          workspaceId
        )
      ),
    setMainFile: (filePath, workspaceId) =>
      request(appendWs("/project/set-main-file", workspaceId), {
        method: "POST",
        body: JSON.stringify({ file_path: filePath }),
      }),
    closeWorkspace: (workspaceId) =>
      request(`/project/workspace/${encodeURIComponent(workspaceId)}`, {
        method: "DELETE",
      }),
  },

  /* ---- 文本校对 ---- */
  typoCheck: {
    preview: (filePath, workspaceId, resolveInput = false) =>
      request(
        appendWs(
          `/typo-check/preview?file_path=${encodeURIComponent(filePath)}&resolve_input=${resolveInput}`,
          workspaceId
        )
      ),
    check: (data, workspaceId) =>
      request(appendWs("/typo-check/check", workspaceId), {
        method: "POST",
        body: JSON.stringify(data),
      }),
    apply: (data, workspaceId) =>
      request(appendWs("/typo-check/apply", workspaceId), {
        method: "POST",
        body: JSON.stringify(data),
      }),
    stream: (workspaceId, filePath, resolveInput = false) =>
      `${BASE_URL}/typo-check/stream/${encodeURIComponent(workspaceId)}?file_path=${encodeURIComponent(filePath)}&resolve_input=${resolveInput}`,
  },

  /* ---- 表达优化 ---- */
  expressionOpt: {
    preview: (filePath, workspaceId, resolveInput = false) =>
      request(
        appendWs(
          `/expression-opt/preview?file_path=${encodeURIComponent(filePath)}&resolve_input=${resolveInput}`,
          workspaceId
        )
      ),
    optimize: (data, workspaceId) =>
      request(appendWs("/expression-opt/optimize", workspaceId), {
        method: "POST",
        body: JSON.stringify(data),
      }),
    apply: (data, workspaceId) =>
      request(appendWs("/expression-opt/apply", workspaceId), {
        method: "POST",
        body: JSON.stringify(data),
      }),
    stream: (workspaceId, filePath, resolveInput = false) =>
      `${BASE_URL}/expression-opt/stream/${encodeURIComponent(workspaceId)}?file_path=${encodeURIComponent(filePath)}&resolve_input=${resolveInput}`,
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
    recognize: (formData) => requestForm("/formula-ocr/recognize", formData),
  },

  /* ---- 表格 OCR 识别（图片上传使用 FormData） ---- */
  tableOCR: {
    recognize: (formData) => requestForm("/table-ocr/recognize", formData),
  },

  /* ---- LaTeX 样式修改 ---- */
  styleModify: {
    modify: (latex, description) =>
      request("/style-modify/modify", {
        method: "POST",
        body: JSON.stringify({ latex, description }),
      }),
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
    preview: (filePath, workspaceId, resolveInput = false) =>
      request(
        appendWs(
          `/formatting/preview?file_path=${encodeURIComponent(filePath)}&resolve_input=${resolveInput}`,
          workspaceId
        )
      ),
    check: (data, workspaceId) =>
      request(appendWs("/formatting/check", workspaceId), {
        method: "POST",
        body: JSON.stringify(data),
      }),
    apply: (data, workspaceId) =>
      request(appendWs("/formatting/apply", workspaceId), {
        method: "POST",
        body: JSON.stringify(data),
      }),
    stream: (workspaceId, filePath, resolveInput = false) =>
      `${BASE_URL}/formatting/stream/${encodeURIComponent(workspaceId)}?file_path=${encodeURIComponent(filePath)}&resolve_input=${resolveInput}`,
  },

  /* ---- 自动编译 ---- */
  compile: {
    run: (mainFile, workspaceId) =>
      request(appendWs("/compile/run", workspaceId), {
        method: "POST",
        body: JSON.stringify({ main_file: mainFile }),
      }),
    pdfUrl: (mainFile, workspaceId) =>
      appendWs(
        `${BASE_URL}/compile/pdf?main_file=${encodeURIComponent(mainFile)}`,
        workspaceId
      ),
  },

  /* ---- Git 差异高亮 ---- */
  gitDiff: {
    refs: (workspaceId) =>
      request(appendWs("/git-diff/refs", workspaceId)),
    renderPdf: ({ mainFile, baseRef, targetRef }, workspaceId) =>
      request(appendWs("/git-diff/render-pdf", workspaceId), {
        method: "POST",
        body: JSON.stringify({
          main_file: mainFile,
          base_ref: baseRef,
          target_ref: targetRef,
        }),
      }),
  },

  /* ---- Markdown 转 LaTeX ---- */
  markdownConverter: {
    convert: (markdown) =>
      request("/markdown-converter/convert", {
        method: "POST",
        body: JSON.stringify({ markdown }),
      }),
  },

  /* ---- 术语一致性检查 ---- */
  terminologyCheck: {
    preview: (filePath, workspaceId, resolveInput = false) =>
      request(
        appendWs(
          `/terminology-check/preview?file_path=${encodeURIComponent(filePath)}&resolve_input=${resolveInput}`,
          workspaceId
        )
      ),
    check: (data, workspaceId) =>
      request(appendWs("/terminology-check/check", workspaceId), {
        method: "POST",
        body: JSON.stringify(data),
      }),
    apply: (data, workspaceId) =>
      request(appendWs("/terminology-check/apply", workspaceId), {
        method: "POST",
        body: JSON.stringify(data),
      }),
    stream: (workspaceId, filePath, resolveInput = false) =>
      `${BASE_URL}/terminology-check/stream/${encodeURIComponent(workspaceId)}?file_path=${encodeURIComponent(filePath)}&resolve_input=${resolveInput}`,
  },

  /* ---- 参考文献管理 ---- */
  bibManager: {
    analyzeFormat: (example) =>
      request("/bib-manager/analyze-format", {
        method: "POST",
        body: JSON.stringify({ example }),
      }),
    extract: (formData) => requestForm("/bib-manager/extract", formData),
    generate: (data) =>
      request("/bib-manager/generate", {
        method: "POST",
        body: JSON.stringify(data),
      }),
    generateLatex: (data) =>
      request("/bib-manager/generate-latex", {
        method: "POST",
        body: JSON.stringify(data),
      }),
    latexStyles: () => request("/bib-manager/latex-styles"),
    readBib: (filePath, workspaceId) =>
      request(appendWs("/bib-manager/read-bib", workspaceId), {
        method: "POST",
        body: JSON.stringify({ file_path: filePath }),
      }),
    save: (data, workspaceId) =>
      request(appendWs("/bib-manager/save", workspaceId), {
        method: "POST",
        body: JSON.stringify(data),
      }),
    listBibFiles: (workspaceId) =>
      request(appendWs("/bib-manager/list-bib-files", workspaceId)),
    formats: {
      list: () => request("/bib-manager/formats"),
      load: (name) =>
        request(`/bib-manager/formats/${encodeURIComponent(name)}`),
      save: (name, formatInfo) =>
        request("/bib-manager/formats", {
          method: "POST",
          body: JSON.stringify({ name, format_info: formatInfo }),
        }),
      delete: (name) =>
        request(`/bib-manager/formats/${encodeURIComponent(name)}`, {
          method: "DELETE",
        }),
    },
  },

  /* ---- 写作助手 Chat ---- */
  writingChat: {
    send: async ({ sessionId, filePath, paragraphIndex, userMessage, assistantPrompt, workspaceId, onEvent, onError }) => {
      const url = appendWs(`${BASE_URL}/writing-chat/send`, workspaceId);
      let res;
      try {
        res = await fetch(url, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            session_id: sessionId ?? null,
            file_path: filePath,
            paragraph_index: paragraphIndex ?? null,
            user_message: userMessage,
            assistant_prompt: assistantPrompt ?? null,
          }),
        });
      } catch (e) {
        throw new Error(translateError(e.message || "Request timed out"));
      }
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: res.statusText }));
        throw new Error(translateError(err.detail || `HTTP ${res.status}`));
      }
      const reader = res.body.getReader();
      const decoder = new TextDecoder("utf-8");
      let buffer = "";
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        let idx;
        while ((idx = buffer.indexOf("\n\n")) !== -1) {
          const frame = buffer.slice(0, idx);
          buffer = buffer.slice(idx + 2);
          if (!frame.startsWith("data:")) continue;
          const jsonStr = frame.slice(5).trim();
          if (!jsonStr) continue;
          try {
            const event = JSON.parse(jsonStr);
            if (onEvent) onEvent(event);
          } catch (e) {
            if (onError) onError(e);
          }
        }
      }
    },
    generateEdit: (data, workspaceId) =>
      request(appendWs("/writing-chat/generate-edit", workspaceId), {
        method: "POST",
        body: JSON.stringify(data),
      }),
    applyEdit: (data, workspaceId) =>
      request(appendWs("/writing-chat/apply-edit", workspaceId), {
        method: "POST",
        body: JSON.stringify(data),
      }),
    deleteMessage: (data) =>
      request("/writing-chat/message/delete", {
        method: "POST",
        body: JSON.stringify(data),
      }),
    undoMessage: (data) =>
      request("/writing-chat/message/undo", {
        method: "POST",
        body: JSON.stringify(data),
      }),
    listSessions: (workspaceId) =>
      request(appendWs("/writing-chat/sessions", workspaceId)),
    loadSession: (id) => request(`/writing-chat/sessions/${id}`),
    deleteSession: (id) =>
      request(`/writing-chat/sessions/${id}`, { method: "DELETE" }),
    renameSession: (id, title) =>
      request(`/writing-chat/sessions/${id}/rename`, {
        method: "PATCH",
        body: JSON.stringify({ session_id: id, title }),
      }),
    assistants: {
      list: () => request("/writing-chat/prompts"),
      load: (name) => request(`/writing-chat/prompts/${encodeURIComponent(name)}`),
      save: (name, content) =>
        request("/writing-chat/prompts", {
          method: "POST",
          body: JSON.stringify({ name, content }),
        }),
      delete: (name) =>
        request(`/writing-chat/prompts/${encodeURIComponent(name)}`, {
          method: "DELETE",
        }),
      setActive: (name) =>
        request("/writing-chat/prompts/active", {
          method: "POST",
          body: JSON.stringify({ name }),
        }),
    },
  },
};
