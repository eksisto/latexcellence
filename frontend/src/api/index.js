/**
 * API 层 —— 封装所有后端接口调用
 *
 * 设计说明：
 * - 后端地址运行时可配置，优先级：设置页（localStorage）→ VITE_API_BASE_URL → 默认同源 /api
 * - 访问令牌：后端配置 security.access_token 后，所有请求必须携带
 *   X-Latexcellence-Token 请求头；下载与 PDF/图片预览通过 fetch + Blob 完成，
 *   只有 EventSource 等确实无法自定义请求头的流式场景才在 URL 上追加 ?token=
 * - request() 自动处理 JSON 序列化/反序列化和错误提取
 * - 后端英文错误经 translateError() 统一翻译为中文后抛出
 * - formulaOCR 模块使用 FormData（图片上传），不走 JSON 请求
 * - 所有需要工作区隔离的端点均携带 workspace_id 查询参数
 */

import { translateError } from "@/utils/errorMessages";
import { getUiLanguage } from "@/i18n";
import {
  appendTokenToUrl,
  normalizeApiBase,
  resolveUrlPath,
} from "@/utils/apiUrl";

const DEFAULT_API_BASE = normalizeApiBase(import.meta.env.VITE_API_BASE_URL);
const STORAGE_BASE_KEY = "latexcellence_api_base";
const STORAGE_TOKEN_KEY = "latexcellence_api_token";
const TOKEN_HEADER = "X-Latexcellence-Token";

/** 当前后端 API 基地址（设置页写入 localStorage，其次构建时环境变量） */
export function getApiBase() {
  try {
    return normalizeApiBase(
      localStorage.getItem(STORAGE_BASE_KEY),
      DEFAULT_API_BASE,
    );
  } catch {
    return DEFAULT_API_BASE;
  }
}

/** 当前访问令牌（后端未启用令牌校验时为空字符串） */
export function getApiToken() {
  try {
    return localStorage.getItem(STORAGE_TOKEN_KEY) || "";
  } catch {
    return "";
  }
}

/** 保存后端连接配置（设置页调用，立即生效） */
export function setApiConnection(apiBase, apiToken) {
  try {
    localStorage.setItem(
      STORAGE_BASE_KEY,
      normalizeApiBase(apiBase, DEFAULT_API_BASE),
    );
    localStorage.setItem(STORAGE_TOKEN_KEY, apiToken || "");
  } catch {
    // 隐私模式等场景 localStorage 不可用，忽略
  }
}

/** 合并访问令牌请求头（未配置令牌时保持原有 headers） */
function authHeaders(extra = {}) {
  const headers = { ...extra };
  const token = getApiToken();
  if (token) headers[TOKEN_HEADER] = token;
  return headers;
}

/** 为无法自定义请求头的 URL（EventSource 等）追加令牌查询参数 */
function appendToken(url) {
  return appendTokenToUrl(url, getApiToken());
}

/** 将后端返回的相对路径解析为完整地址（不追加令牌，实现见 utils/apiUrl.js） */
export function resolveUrl(path) {
  return resolveUrlPath(path, getApiBase(), window.location.origin);
}

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
    res = await fetch(`${getApiBase()}${url}`, {
      method,
      body: formData,
      headers: authHeaders(),
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

async function consumeSse(res, onEvent, onError) {
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
        if (onEvent) onEvent(JSON.parse(jsonStr));
      } catch (e) {
        if (onError) onError(e);
      }
    }
  }
  if (buffer.startsWith("data:")) {
    const jsonStr = buffer.slice(5).trim();
    if (jsonStr) {
      try {
        if (onEvent) onEvent(JSON.parse(jsonStr));
      } catch (e) {
        if (onError) onError(e);
      }
    }
  }
}

/**
 * 下载文件（fetch + blob），失败时抛出已翻译的错误
 * @param {string} url  下载地址
 * @param {string} fallbackName  无 Content-Disposition 时的兜底文件名
 */
/** 以带认证头的 fetch 获取二进制资源，返回临时 blob URL（调用方负责 revoke） */
export async function fetchBlobUrl(url) {
  const res = await fetch(url, { headers: authHeaders() });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(translateError(err.detail || `HTTP ${res.status}`));
  }
  return URL.createObjectURL(await res.blob());
}

export async function downloadBlob(url, fallbackName) {
  const res = await fetch(url, { headers: authHeaders() });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(translateError(err.detail || `HTTP ${res.status}`));
  }
  const blob = await res.blob();
  const cd = res.headers.get("content-disposition") || "";
  const match = /filename="?([^";]+)"?/i.exec(cd);
  const filename = (match && match[1]) || fallbackName;
  const objectUrl = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = objectUrl;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(objectUrl);
}

/**
 * 通用请求函数
 * @param {string} url     API 路径（如 "/health"）
 * @param {object} options fetch 选项（method, body, headers 等）
 * @returns {Promise<object>} 解析后的 JSON 响应
 */
async function request(url, options = {}) {
  const { headers: optionHeaders, ...restOptions } = options;
  let res;
  try {
    res = await fetch(`${getApiBase()}${url}`, {
      ...restOptions,
      headers: authHeaders({
        "Content-Type": "application/json",
        ...optionHeaders,
      }),
    });
  } catch (e) {
    // 保留取消信号（AbortController）的原始错误，让调用方区分“主动取消”和“真的失败”
    if (e?.name === "AbortError") throw e;
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
  /** 连接测试：health 验证可达性，config/status 验证令牌是否有效 */
  checkConnection: async (apiBase, apiToken) => {
    const base = (apiBase || getApiBase()).replace(/\/+$/, "");
    const headers = { "Content-Type": "application/json" };
    if (apiToken) headers[TOKEN_HEADER] = apiToken;
    try {
      const healthRes = await fetch(`${base}/health`, { headers });
      if (!healthRes.ok) {
        const err = await healthRes
          .json()
          .catch(() => ({ detail: healthRes.statusText }));
        throw new Error(
          translateError(err.detail || `HTTP ${healthRes.status}`),
        );
      }
      const health = await healthRes.json();
      const statusRes = await fetch(`${base}/config/status`, { headers });
      if (!statusRes.ok) {
        const err = await statusRes
          .json()
          .catch(() => ({ detail: statusRes.statusText }));
        throw new Error(
          translateError(err.detail || `HTTP ${statusRes.status}`),
        );
      }
      const status = await statusRes.json();
      return { ...health, ...status };
    } catch (e) {
      // fetch 网络错误（Failed to fetch / Load failed 等）同样需要翻译
      throw new Error(translateError(e.message || "Request timed out"));
    }
  },

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
          workspaceId,
        ),
      ),
    applyChange: (
      filePath,
      paragraphIndex,
      newText,
      workspaceId,
      expectedMd5 = null,
    ) =>
      request(appendWs("/project/apply-change", workspaceId), {
        method: "POST",
        body: JSON.stringify({
          file_path: filePath,
          paragraph_index: paragraphIndex,
          new_text: newText,
          expected_md5: expectedMd5,
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
    undoStatus: (filePath, workspaceId, options = {}) =>
      request(
        appendWs(
          `/project/undo-status?file_path=${encodeURIComponent(filePath)}`,
          workspaceId,
        ),
        options,
      ),
    setMainFile: (filePath, workspaceId) =>
      request(appendWs("/project/set-main-file", workspaceId), {
        method: "POST",
        body: JSON.stringify({ file_path: filePath }),
      }),
    fileMd5: (filePath, workspaceId) =>
      request(
        appendWs(
          `/project/file-md5?file_path=${encodeURIComponent(filePath)}`,
          workspaceId,
        ),
      ),
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
          workspaceId,
        ),
      ),
    check: (data, workspaceId) =>
      request(appendWs("/typo-check/check", workspaceId), {
        method: "POST",
        body: JSON.stringify({ ...data, language: getUiLanguage() }),
      }),
    apply: (data, workspaceId) =>
      request(appendWs("/typo-check/apply", workspaceId), {
        method: "POST",
        body: JSON.stringify(data),
      }),
    stream: (workspaceId, filePath, resolveInput = false) =>
      appendToken(
        `${getApiBase()}/typo-check/stream/${encodeURIComponent(workspaceId)}?file_path=${encodeURIComponent(filePath)}&resolve_input=${resolveInput}&language=${getUiLanguage()}`,
      ),
  },

  /* ---- 表达优化 ---- */
  expressionOpt: {
    preview: (filePath, workspaceId, resolveInput = false) =>
      request(
        appendWs(
          `/expression-opt/preview?file_path=${encodeURIComponent(filePath)}&resolve_input=${resolveInput}`,
          workspaceId,
        ),
      ),
    optimize: (data, workspaceId) =>
      request(appendWs("/expression-opt/optimize", workspaceId), {
        method: "POST",
        body: JSON.stringify({ ...data, language: getUiLanguage() }),
      }),
    apply: (data, workspaceId) =>
      request(appendWs("/expression-opt/apply", workspaceId), {
        method: "POST",
        body: JSON.stringify(data),
      }),
    stream: (workspaceId, filePath, resolveInput = false) =>
      appendToken(
        `${getApiBase()}/expression-opt/stream/${encodeURIComponent(workspaceId)}?file_path=${encodeURIComponent(filePath)}&resolve_input=${resolveInput}&language=${getUiLanguage()}`,
      ),
    prompts: {
      list: () => request("/expression-opt/prompts"),
      load: (name) =>
        request(`/expression-opt/prompts/${encodeURIComponent(name)}`),
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
    agent: {
      run: async ({ workspaceId, description, signal, onEvent, onError }) => {
        const res = await fetch(`${getApiBase()}/style-modify/agent/run`, {
          method: "POST",
          headers: authHeaders({ "Content-Type": "application/json" }),
          body: JSON.stringify({
            description,
            workspace_id: workspaceId,
            language: getUiLanguage(),
          }),
          signal,
        });
        if (!res.ok) {
          const err = await res
            .json()
            .catch(() => ({ detail: res.statusText }));
          throw new Error(translateError(err.detail || `HTTP ${res.status}`));
        }
        await consumeSse(res, onEvent, onError);
      },
      refine: async ({
        sessionId,
        workspaceId,
        message,
        signal,
        onEvent,
        onError,
      }) => {
        const res = await fetch(
          `${getApiBase()}/style-modify/agent/sessions/${encodeURIComponent(sessionId)}/refine`,
          {
            method: "POST",
            headers: authHeaders({ "Content-Type": "application/json" }),
            body: JSON.stringify({
              workspace_id: workspaceId,
              message,
              language: getUiLanguage(),
            }),
            signal,
          },
        );
        if (!res.ok) {
          const err = await res
            .json()
            .catch(() => ({ detail: res.statusText }));
          throw new Error(translateError(err.detail || `HTTP ${res.status}`));
        }
        await consumeSse(res, onEvent, onError);
      },
      session: (sessionId, workspaceId) =>
        request(
          appendWs(
            `/style-modify/agent/sessions/${encodeURIComponent(sessionId)}`,
            workspaceId,
          ),
        ),
      trace: (sessionId, workspaceId) =>
        request(
          appendWs(
            `/style-modify/agent/sessions/${encodeURIComponent(sessionId)}/trace`,
            workspaceId,
          ),
        ),
      downloadTrace: async (sessionId, workspaceId) => {
        await downloadBlob(
          appendWs(
            `${getApiBase()}/style-modify/agent/sessions/${encodeURIComponent(sessionId)}/trace/download`,
            workspaceId,
          ),
          `style-modify-${sessionId.slice(0, 8)}-trace.json`,
        );
      },
      apply: (sessionId, revision, workspaceId) =>
        request(
          appendWs(
            `/style-modify/agent/sessions/${encodeURIComponent(sessionId)}/apply`,
            workspaceId,
          ),
          {
            method: "POST",
            body: JSON.stringify({ revision, workspace_id: workspaceId }),
          },
        ),
      undo: (operationId, workspaceId) =>
        request(
          `/style-modify/agent/operations/${encodeURIComponent(operationId)}/undo`,
          {
            method: "POST",
            body: JSON.stringify({ workspace_id: workspaceId }),
          },
        ),
      delete: (sessionId, workspaceId) =>
        request(
          appendWs(
            `/style-modify/agent/sessions/${encodeURIComponent(sessionId)}`,
            workspaceId,
          ),
          { method: "DELETE" },
        ),
    },
  },

  /* ---- 模板生成 ---- */
  templateGen: {
    /* 自主 Agent 生成（SSE 流式） */
    generate: async ({ description, language, onEvent, onError }) => {
      const res = await fetch(`${getApiBase()}/template-gen/generate`, {
        method: "POST",
        headers: authHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({
          description,
          language: language ?? getUiLanguage(),
        }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: res.statusText }));
        throw new Error(translateError(err.detail || `HTTP ${res.status}`));
      }
      await consumeSse(res, onEvent, onError);
    },
    files: (sessionId) =>
      request(
        `/template-gen/files?session_id=${encodeURIComponent(sessionId)}`,
      ),
    file: (sessionId, path) =>
      request(
        `/template-gen/file?session_id=${encodeURIComponent(sessionId)}&path=${encodeURIComponent(path)}`,
      ),
    trace: (sessionId) =>
      request(
        `/template-gen/trace?session_id=${encodeURIComponent(sessionId)}`,
      ),
    downloadTrace: async (sessionId) => {
      await downloadBlob(
        `${getApiBase()}/template-gen/trace/download?session_id=${encodeURIComponent(sessionId)}`,
        `latex-template-${sessionId.slice(0, 8)}-trace.json`,
      );
    },
    download: async (sessionId) => {
      await downloadBlob(
        `${getApiBase()}/template-gen/download?session_id=${encodeURIComponent(sessionId)}`,
        `latex-template-${sessionId.slice(0, 8)}.zip`,
      );
    },
  },

  /* ---- LaTeX 代码审查 ---- */
  codeReview: {
    preview: (filePath, workspaceId, resolveInput = false) =>
      request(
        appendWs(
          `/code-review/preview?file_path=${encodeURIComponent(filePath)}&resolve_input=${resolveInput}`,
          workspaceId,
        ),
      ),
    check: (data, workspaceId) =>
      request(appendWs("/code-review/check", workspaceId), {
        method: "POST",
        body: JSON.stringify({ ...data, language: getUiLanguage() }),
      }),
    apply: (data, workspaceId) =>
      request(appendWs("/code-review/apply", workspaceId), {
        method: "POST",
        body: JSON.stringify(data),
      }),
    stream: (workspaceId, filePath, resolveInput = false) =>
      appendToken(
        `${getApiBase()}/code-review/stream/${encodeURIComponent(workspaceId)}?file_path=${encodeURIComponent(filePath)}&resolve_input=${resolveInput}&language=${getUiLanguage()}`,
      ),
  },

  /* ---- 自动编译 ---- */
  compile: {
    run: (mainFile, workspaceId, options = {}) =>
      request(appendWs("/compile/run", workspaceId), {
        method: "POST",
        body: JSON.stringify({
          main_file: mainFile,
          language: getUiLanguage(),
          engine: options.engine || "auto",
          pdfa: options.pdfa || false,
        }),
      }),
    pdfBlobUrl: async (mainFile, workspaceId) => {
      const url = appendWs(
        `${getApiBase()}/compile/pdf?main_file=${encodeURIComponent(mainFile)}`,
        workspaceId,
      );
      return fetchBlobUrl(url);
    },
  },

  /* ---- Git 差异高亮 ---- */
  gitDiff: {
    refs: (workspaceId) => request(appendWs("/git-diff/refs", workspaceId)),
    renderPdf: (
      {
        mainFile,
        baseRef,
        targetRef,
        diffEngine = "difflib",
        markupType = "UNDERLINE",
        mathMarkup = "fine",
        colorAdd = "",
        colorDel = "",
        excludeSafecmd = "",
        excludeTextcmd = "",
      },
      workspaceId,
    ) =>
      request(appendWs("/git-diff/render-pdf", workspaceId), {
        method: "POST",
        body: JSON.stringify({
          main_file: mainFile,
          base_ref: baseRef,
          target_ref: targetRef,
          diff_engine: diffEngine,
          markup_type: markupType,
          math_markup: mathMarkup,
          color_add: colorAdd,
          color_del: colorDel,
          exclude_safecmd: excludeSafecmd,
          exclude_textcmd: excludeTextcmd,
        }),
      }),
    getOptions: () => request(appendWs("/git-diff/options")),
  },

  /* ---- 文档格式转换（Markdown/DOCX/HTML ↔ LaTeX） ---- */
  formatConverter: {
    convert: (markdown) =>
      request("/format-converter/convert", {
        method: "POST",
        body: JSON.stringify({ markdown }),
      }),
    convertText: (text, sourceFormat, targetFormat) =>
      request("/format-converter/convert-text", {
        method: "POST",
        body: JSON.stringify({
          text,
          source_format: sourceFormat,
          target_format: targetFormat,
        }),
      }).then((data) => {
        if (data.is_binary && typeof data.result === "string") {
          const bytes = Uint8Array.from(atob(data.result), (c) =>
            c.charCodeAt(0),
          );
          data.result = new Blob([bytes]);
        }
        return data;
      }),
    convertFile: (file, sourceFormat, targetFormat) => {
      const formData = new FormData();
      formData.append("file", file);
      return requestForm(
        `/format-converter/convert-file?source_format=${encodeURIComponent(sourceFormat)}&target_format=${encodeURIComponent(targetFormat)}`,
        formData,
      ).then((data) => {
        if (data.is_binary && typeof data.result === "string") {
          const bytes = Uint8Array.from(atob(data.result), (c) =>
            c.charCodeAt(0),
          );
          data.result = new Blob([bytes]);
        }
        return data;
      });
    },
  },

  /* ---- 术语一致性检查 ---- */
  terminologyCheck: {
    preview: (filePath, workspaceId, resolveInput = false) =>
      request(
        appendWs(
          `/terminology-check/preview?file_path=${encodeURIComponent(filePath)}&resolve_input=${resolveInput}`,
          workspaceId,
        ),
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
      appendToken(
        `${getApiBase()}/terminology-check/stream/${encodeURIComponent(workspaceId)}?file_path=${encodeURIComponent(filePath)}&resolve_input=${resolveInput}`,
      ),
  },

  /* ---- 逐段翻译 ---- */
  paragraphTranslate: {
    preview: (filePath, workspaceId, resolveInput = false) =>
      request(
        appendWs(
          `/paragraph-translate/preview?file_path=${encodeURIComponent(filePath)}&resolve_input=${resolveInput}`,
          workspaceId,
        ),
      ),
    translate: (data, workspaceId) =>
      request(appendWs("/paragraph-translate/translate", workspaceId), {
        method: "POST",
        body: JSON.stringify({ ...data, language: getUiLanguage() }),
      }),
    apply: (data, workspaceId) =>
      request(appendWs("/paragraph-translate/apply", workspaceId), {
        method: "POST",
        body: JSON.stringify(data),
      }),
    stream: (
      workspaceId,
      filePath,
      resolveInput = false,
      targetLanguage = "English",
    ) =>
      appendToken(
        `${getApiBase()}/paragraph-translate/stream/${encodeURIComponent(workspaceId)}?file_path=${encodeURIComponent(filePath)}&resolve_input=${resolveInput}&language=${getUiLanguage()}&target_language=${encodeURIComponent(targetLanguage)}`,
      ),
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
        body: JSON.stringify({ ...data, language: getUiLanguage() }),
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
    doiLookup: (doi) =>
      request("/bib-manager/doi-lookup", {
        method: "POST",
        body: JSON.stringify({ doi }),
      }),
    generateFromDoi: (data) =>
      request("/bib-manager/generate-from-doi", {
        method: "POST",
        body: JSON.stringify(data),
      }),
    parseEntries: (filePath, workspaceId) =>
      request(appendWs("/bib-manager/parse-entries", workspaceId), {
        method: "POST",
        body: JSON.stringify({ file_path: filePath }),
      }),
    detectDuplicates: (filePath, workspaceId) =>
      request(appendWs("/bib-manager/detect-duplicates", workspaceId), {
        method: "POST",
        body: JSON.stringify(filePath ? { file_path: filePath } : {}),
      }),
    deleteEntry: (filePath, citeKey, workspaceId) =>
      request(appendWs("/bib-manager/delete-entry", workspaceId), {
        method: "POST",
        body: JSON.stringify({ file_path: filePath, cite_key: citeKey }),
      }),
    updateEntry: (filePath, citeKey, bibtexCode, workspaceId) =>
      request(appendWs("/bib-manager/update-entry", workspaceId), {
        method: "POST",
        body: JSON.stringify({
          file_path: filePath,
          cite_key: citeKey,
          bibtex_code: bibtexCode,
        }),
      }),
    entryStats: (filePath, workspaceId) =>
      request(
        appendWs(
          `/bib-manager/entry-stats?file_path=${encodeURIComponent(filePath)}`,
          workspaceId,
        ),
      ),
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

  /* ---- 科研绘图 ---- */
  plotGen: {
    upload: (formData) => requestForm("/plot-gen/upload", formData),
    styles: () => request("/plot-gen/styles"),
    generate: async ({
      sessionId,
      description,
      style,
      signal,
      onEvent,
      onError,
    }) => {
      const res = await fetch(`${getApiBase()}/plot-gen/agent/generate`, {
        method: "POST",
        headers: authHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({
          session_id: sessionId,
          description,
          style,
          language: getUiLanguage(),
        }),
        signal,
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: res.statusText }));
        throw new Error(translateError(err.detail || `HTTP ${res.status}`));
      }
      await consumeSse(res, onEvent, onError);
    },
    refine: async ({ sessionId, message, signal, onEvent, onError }) => {
      const res = await fetch(
        `${getApiBase()}/plot-gen/sessions/${encodeURIComponent(sessionId)}/refine`,
        {
          method: "POST",
          headers: authHeaders({ "Content-Type": "application/json" }),
          body: JSON.stringify({
            message,
            language: getUiLanguage(),
          }),
          signal,
        },
      );
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: res.statusText }));
        throw new Error(translateError(err.detail || `HTTP ${res.status}`));
      }
      await consumeSse(res, onEvent, onError);
    },
    files: (sessionId) =>
      request(`/plot-gen/sessions/${encodeURIComponent(sessionId)}/files`),
    file: (sessionId, path) =>
      request(
        `/plot-gen/sessions/${encodeURIComponent(sessionId)}/file?path=${encodeURIComponent(path)}`,
      ),
    previewBlobUrl: async (sessionId, path) => {
      const url = `${getApiBase()}/plot-gen/sessions/${encodeURIComponent(sessionId)}/preview?path=${encodeURIComponent(path)}`;
      return fetchBlobUrl(url);
    },
    trace: (sessionId) =>
      request(`/plot-gen/sessions/${encodeURIComponent(sessionId)}/trace`),
    downloadTrace: async (sessionId) => {
      await downloadBlob(
        `${getApiBase()}/plot-gen/sessions/${encodeURIComponent(sessionId)}/trace/download`,
        `plot-gen-${sessionId.slice(0, 8)}-trace.json`,
      );
    },
    download: async (sessionId) => {
      await downloadBlob(
        `${getApiBase()}/plot-gen/sessions/${encodeURIComponent(sessionId)}/download`,
        `plot-gen-${sessionId.slice(0, 8)}.zip`,
      );
    },
  },

  /* ---- 写作助手 ---- */
  writingChat: {
    send: async ({
      sessionId,
      filePath,
      paragraphIndex,
      userMessage,
      assistantPrompt,
      workspaceId,
      onEvent,
      onError,
    }) => {
      const url = appendWs(`${getApiBase()}/writing-chat/send`, workspaceId);
      let res;
      try {
        res = await fetch(url, {
          method: "POST",
          headers: authHeaders({ "Content-Type": "application/json" }),
          body: JSON.stringify({
            session_id: sessionId ?? null,
            file_path: filePath,
            paragraph_index: paragraphIndex ?? null,
            user_message: userMessage,
            assistant_prompt: assistantPrompt ?? null,
            language: getUiLanguage(),
          }),
        });
      } catch (e) {
        throw new Error(translateError(e.message || "Request timed out"));
      }
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: res.statusText }));
        throw new Error(translateError(err.detail || `HTTP ${res.status}`));
      }
      await consumeSse(res, onEvent, onError);
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
    clarify: (clarifyId, answer) =>
      request("/writing-chat/clarify", {
        method: "POST",
        body: JSON.stringify({ clarify_id: clarifyId, answer }),
      }),
    cancelClarify: (clarifyId) =>
      request("/writing-chat/cancel-clarify", {
        method: "POST",
        body: JSON.stringify({ clarify_id: clarifyId }),
      }),
    assistants: {
      list: () => request("/writing-chat/prompts"),
      load: (name) =>
        request(`/writing-chat/prompts/${encodeURIComponent(name)}`),
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
    submitDDGChallenge: (challengeId, selectedHashes) =>
      request("/search/ddg-challenge", {
        method: "POST",
        body: JSON.stringify({
          challenge_id: challengeId,
          selected_hashes: selectedHashes,
        }),
      }),
    confirmTool: (pendingId, modifiedArgs) =>
      request("/writing-chat/confirm-tool", {
        method: "POST",
        body: JSON.stringify({
          pending_id: pendingId,
          modified_args: modifiedArgs || null,
        }),
      }),
    cancelTool: (pendingId) =>
      request("/writing-chat/cancel-tool", {
        method: "POST",
        body: JSON.stringify({
          pending_id: pendingId,
        }),
      }),
  },
};
