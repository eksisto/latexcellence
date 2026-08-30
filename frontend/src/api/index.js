/**
 * API 层 —— 封装所有后端接口调用
 *
 * 设计说明：
 * - 后端地址运行时可配置，优先级：设置页（localStorage）→ VITE_API_BASE_URL → 默认 http://localhost:8000/api
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
  appendToken,
  authHeaders,
  checkConnection,
  getApiBase,
  getApiToken,
  notifyUnauthorized,
  resolveUrl,
  setApiConnection,
} from "./connection";

// 兼容导出：连接相关能力统一实现在 ./connection，外部仍可经 "@/api" 引入
export {
  getApiBase,
  getApiToken,
  resolveUrl,
  setApiConnection,
  isMultiUser as getMultiUserMode,
} from "./connection";

/** 401 响应：通知全局（会话过期 → 登出并跳转登录页） */
function checkUnauthorized(res) {
  if (res && res.status === 401) notifyUnauthorized();
}

/**
 * 构造带错误码的请求错误：message 为翻译后的错误文本；
 * code 为后端结构化 detail.error（如 account_pending / no_permission），
 * 供调用方按错误码分支（如登录失败时区分"待审批"与"密码错误"）。
 */
function buildRequestError(err, status) {
  const error = new Error(translateError(err?.detail || `HTTP ${status}`));
  const detail = err?.detail;
  if (
    detail &&
    typeof detail === "object" &&
    typeof detail.error === "string"
  ) {
    error.code = detail.error;
  }
  return error;
}

/**
 * 统一解析非 2xx 响应并抛出已翻译的请求错误：
 * 401 触发全局登出回调（notifyUnauthorized）；响应体 JSON 解析失败时
 * 以 statusText 兜底。仅收敛与该实现逐字一致的错误块——request() 因
 * invalid_credentials 登录豁免与结构化 code 保留等差异保持独立实现。
 */
async function parseErrorResponse(res) {
  checkUnauthorized(res);
  const err = await res.json().catch(() => ({ detail: res.statusText }));
  throw buildRequestError(err, res.status);
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
      // 多用户模式认证依赖 Cookie 会话；跨源部署（如 Vite dev 直连后端）
      // 时默认的 "same-origin" 不会携带/写入 Cookie，必须显式 include
      credentials: "include",
    });
  } catch (e) {
    throw new Error(translateError(e.message || "Request timed out"));
  }
  if (!res.ok) {
    await parseErrorResponse(res);
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
  const res = await fetch(url, {
    headers: authHeaders(),
    credentials: "include",
  });
  if (!res.ok) {
    await parseErrorResponse(res);
  }
  return URL.createObjectURL(await res.blob());
}

export async function downloadBlob(url, fallbackName) {
  const res = await fetch(url, {
    headers: authHeaders(),
    credentials: "include",
  });
  if (!res.ok) {
    await parseErrorResponse(res);
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
      // 多用户模式认证依赖 Cookie 会话；跨源部署时必须显式携带凭据
      credentials: "include",
    });
  } catch (e) {
    // 保留取消信号（AbortController）的原始错误，让调用方区分“主动取消”和“真的失败”
    if (e?.name === "AbortError") throw e;
    throw new Error(translateError(e.message || "Request timed out"));
  }
  if (!res.ok) {
    const err = await res.json().catch(() => null);
    const detail = err?.detail ?? res.statusText;
    // 登录凭据错误（401 invalid_credentials）是登录失败而非会话过期，
    // 不触发全局登出回调（否则登录页会被误清状态）
    if (detail?.error !== "invalid_credentials") {
      checkUnauthorized(res);
    }
    // 必须继续经 buildRequestError 抛出：结构化 detail.error 会挂到
    // error.code，登录页 account_pending 分支依赖它导向 /waiting 待审批页。
    // 裸 new Error(translateError(...)) 会丢掉 code，使 pending 状态机失联；
    // { detail } 包装在 detail 兜底为 statusText 时保持错误消息行为不变。
    throw buildRequestError({ detail }, res.status);
  }
  return res.json();
}

export const api = {
  /* ---- 系统 ---- */
  health: () => request("/health"),
  configStatus: () => request("/config/status"),
  /** 连接测试：health 验证可达性，config/status 验证令牌是否有效 */
  checkConnection,

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
    /** 多用户模式：按 project_id 打开项目（拒绝任意路径） */
    openById: (projectId, workspaceId) =>
      request(appendWs("/project/open", workspaceId), {
        method: "POST",
        body: JSON.stringify({ project_id: projectId }),
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
    /** 编辑器打开文件：返回 { file_path, content, md5 }（editor-plan §5.1） */
    fileContent: (filePath, workspaceId) =>
      request(
        appendWs(
          `/project/file-content?file_path=${encodeURIComponent(filePath)}`,
          workspaceId,
        ),
      ),
    /** 编辑器二进制预览（当前仅 PDF）：返回临时 blob URL，调用方负责 revoke */
    rawFileBlobUrl: async (filePath, workspaceId) => {
      const url = appendWs(
        `${getApiBase()}/project/raw-file?file_path=${encodeURIComponent(filePath)}`,
        workspaceId,
      );
      return fetchBlobUrl(url);
    },
    /**
     * 编辑器整文件保存（MD5 乐观锁）：base_md5 与磁盘不符时后端返回
     * 409 结构化错误（error: "file_conflict"）；成功返回 { status, file_path, md5 }
     */
    saveFile: (filePath, content, baseMd5, workspaceId) =>
      request(appendWs("/project/save-file", workspaceId), {
        method: "POST",
        body: JSON.stringify({
          file_path: filePath,
          content,
          base_md5: baseMd5,
          // save-file 的权限依赖从 JSON body 读 workspace_id（SaveFileRequest
          // 亦要求此字段），仅拼在 query 上会被判 invalid_workspace
          workspace_id: workspaceId,
        }),
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
          credentials: "include",
        });
        if (!res.ok) {
          await parseErrorResponse(res);
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
            credentials: "include",
          },
        );
        if (!res.ok) {
          await parseErrorResponse(res);
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
        credentials: "include",
      });
      if (!res.ok) {
        await parseErrorResponse(res);
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
    /** 内联预览产物（当前仅 PDF）：返回临时 blob URL，调用方负责 revoke */
    preview: async (sessionId, path) =>
      fetchBlobUrl(
        `${getApiBase()}/template-gen/preview?session_id=${encodeURIComponent(sessionId)}&path=${encodeURIComponent(path)}`,
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

  /* ---- Git 版本管理（状态 / 初始化 / 提交 / 历史） ---- */
  gitManage: {
    status: (workspaceId) =>
      request(appendWs("/git-manage/status", workspaceId)),
    init: (workspaceId) =>
      request(appendWs("/git-manage/init", workspaceId), { method: "POST" }),
    commit: (message, workspaceId) =>
      request(appendWs("/git-manage/commit", workspaceId), {
        method: "POST",
        body: JSON.stringify({ message }),
      }),
    log: (workspaceId, limit = 50, offset = 0) =>
      request(
        appendWs("/git-manage/log", workspaceId) +
          `&limit=${limit}&offset=${offset}`,
      ),
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
        credentials: "include",
      });
      if (!res.ok) {
        await parseErrorResponse(res);
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
          credentials: "include",
        },
      );
      if (!res.ok) {
        await parseErrorResponse(res);
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
          credentials: "include",
        });
      } catch (e) {
        throw new Error(translateError(e.message || "Request timed out"));
      }
      if (!res.ok) {
        await parseErrorResponse(res);
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
    compactSession: (id) =>
      request(`/writing-chat/sessions/${encodeURIComponent(id)}/compact`, {
        method: "POST",
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
    /**
     * 编辑器助手流式对话（editor-plan §5.2，阶段四）。
     * 携带 CM6 缓冲区全文与光标/选区元信息；事件经 consumeSse 逐帧回调。
     * @param {AbortSignal} [signal] 取消在途请求（项目切换/组件卸载时中止
     *   流）；abort 使 fetch 与读循环以 AbortError 结束，由调用方静默处理
     */
    editorSend: async ({
      sessionId,
      filePath,
      buffer,
      cursorLine,
      selection,
      includeFullFile,
      userMessage,
      workspaceId,
      onEvent,
      onError,
      signal,
    }) => {
      const url = appendWs(
        `${getApiBase()}/writing-chat/editor/send`,
        workspaceId,
      );
      let res;
      try {
        res = await fetch(url, {
          method: "POST",
          headers: authHeaders({ "Content-Type": "application/json" }),
          body: JSON.stringify({
            session_id: sessionId ?? null,
            file_path: filePath ?? "",
            buffer: buffer ?? "",
            cursor_line: cursorLine ?? null,
            selection: selection ?? "",
            include_full_file: includeFullFile ?? false,
            user_message: userMessage,
            language: getUiLanguage(),
          }),
          signal,
          credentials: "include",
        });
      } catch (e) {
        // 保留取消信号（AbortController）的原始错误，让调用方区分“主动取消”和“真的失败”
        if (e?.name === "AbortError") throw e;
        throw new Error(translateError(e.message || "Request timed out"));
      }
      if (!res.ok) {
        await parseErrorResponse(res);
      }
      // 读循环随 abort 自然抛错结束（res.body 流被取消）
      await consumeSse(res, onEvent, onError);
    },
    /**
     * 记录编辑器建议应用结果（仅登记状态，无文件写入）。
     * @param {number|null} [suggestionIndex] 本次应用建议的逐条 index；
     *   响应携带 applied_suggestions（该消息已应用 index 全量列表），供
     *   调用方回写权威状态
     */
    applyEditorSuggestion: (
      sessionId,
      messageId,
      workspaceId,
      suggestionIndex = null,
    ) =>
      request(appendWs("/writing-chat/editor/apply-suggestion", workspaceId), {
        method: "POST",
        body: JSON.stringify({
          session_id: sessionId,
          message_id: messageId,
          suggestion_index: suggestionIndex ?? null,
        }),
      }),
  },

  /* ---- 多用户认证（/api/auth） ---- */
  auth: {
    register: (data) =>
      request("/auth/register", {
        method: "POST",
        body: JSON.stringify(data),
      }),
    login: (data) =>
      request("/auth/login", {
        method: "POST",
        body: JSON.stringify({
          username: data.username,
          password: data.password,
          remember: !!data.remember,
        }),
      }),
    logout: () =>
      request("/auth/logout", {
        method: "POST",
      }),
    me: () => request("/auth/me"),
    // 自助清理本人指定类别衍生数据（写作会话/样式会话/模板/绘图）
    cleanupDerivedData: (category) =>
      request("/auth/cleanup-derived-data", {
        method: "POST",
        body: JSON.stringify({ category }),
      }),
    // 自助找回密码：输入用户名或邮箱（公开端点，响应恒定语义防枚举；
    // delivery=sent 表示邮件已投递，unavailable 引导联系管理员）
    forgotPassword: (identifier) =>
      request("/auth/forgot-password", {
        method: "POST",
        body: JSON.stringify({ identifier }),
      }),
    // 凭邮件令牌设置新密码（公开端点；成功后全部会话被吊销）
    resetPassword: (token, newPassword) =>
      request("/auth/reset-password", {
        method: "POST",
        body: JSON.stringify({ token, new_password: newPassword }),
      }),
    // 绑定/修改邮箱：需当前密码确认（email 传空字符串表示解绑）
    updateProfile: (currentPassword, email) =>
      request("/auth/profile", {
        method: "PUT",
        body: JSON.stringify({ current_password: currentPassword, email }),
      }),
  },

  /* ---- 多用户管理后台（/api/admin，仅 admin） ---- */
  admin: {
    listUsers: () => request("/admin/users"),
    approve: (userId) =>
      request(`/admin/users/${encodeURIComponent(userId)}/approve`, {
        method: "POST",
      }),
    reject: (userId) =>
      request(`/admin/users/${encodeURIComponent(userId)}/reject`, {
        method: "POST",
      }),
    disable: (userId) =>
      request(`/admin/users/${encodeURIComponent(userId)}/disable`, {
        method: "POST",
      }),
    enable: (userId) =>
      request(`/admin/users/${encodeURIComponent(userId)}/enable`, {
        method: "POST",
      }),
    resetPasswordPreview: (userId) =>
      request(
        `/admin/users/${encodeURIComponent(userId)}/reset-password/preview`,
        {
          method: "POST",
        },
      ),
    resetPasswordConfirm: (userId, tempPassword) =>
      request(
        `/admin/users/${encodeURIComponent(userId)}/reset-password/confirm`,
        {
          method: "POST",
          body: JSON.stringify({ temp_password: tempPassword }),
        },
      ),
    setQuota: (userId, data) =>
      request(`/admin/users/${encodeURIComponent(userId)}/quota`, {
        method: "PUT",
        body: JSON.stringify({
          project_quota_bytes: data.projectQuotaBytes,
          derived_quota_bytes: data.derivedQuotaBytes,
        }),
      }),
    deleteUser: (userId) =>
      request(`/admin/users/${encodeURIComponent(userId)}`, {
        method: "DELETE",
      }),
  },

  /* ---- 多用户项目（/api/projects） ---- */
  projects: {
    list: () => request("/projects"),
    recycleBin: () => request("/projects/recycle-bin"),
    /** 上传压缩包创建项目（zip/7z/rar，FormData；name 可选） */
    upload: (file, name, onProgress) => {
      const formData = new FormData();
      // 目录打包产物是 Blob（无 name），必须显式给 filename，
      // 否则浏览器默认填 "blob"，后端按扩展名白名单拒绝（400 invalid_archive）
      formData.append("archive", file, file.name || "upload.zip");
      if (name) formData.append("name", name);
      return new Promise((resolve, reject) => {
        const xhr = new XMLHttpRequest();
        xhr.open("POST", `${getApiBase()}/projects`);
        // 跨源部署时携带 Cookie（多用户会话认证）
        xhr.withCredentials = true;
        const headers = authHeaders();
        Object.entries(headers).forEach(([k, v]) => xhr.setRequestHeader(k, v));
        // onprogress 高频触发（高速网络可达几十毫秒一次），按取整百分比去重，
        // 避免大文件上传期间调用方（对话框 setProgress）无谓高频重渲染
        let lastProgress = -1;
        xhr.upload.onprogress = (e) => {
          if (onProgress && e.lengthComputable) {
            const pct = Math.round((e.loaded / e.total) * 100);
            if (pct !== lastProgress) {
              lastProgress = pct;
              onProgress(pct);
            }
          }
        };
        xhr.onload = () => {
          const parse = () => {
            try {
              return JSON.parse(xhr.responseText || "{}");
            } catch {
              return {};
            }
          };
          if (xhr.status >= 200 && xhr.status < 300) {
            resolve(parse());
          } else {
            if (xhr.status === 401) notifyUnauthorized();
            reject(buildRequestError(parse(), xhr.status));
          }
        };
        xhr.onerror = () =>
          reject(new Error(translateError("Failed to fetch")));
        xhr.send(formData);
      });
    },
    rename: (projectId, name) =>
      request(`/projects/${encodeURIComponent(projectId)}`, {
        method: "PUT",
        body: JSON.stringify({ name }),
      }),
    /** 进回收站 */
    remove: (projectId) =>
      request(`/projects/${encodeURIComponent(projectId)}`, {
        method: "DELETE",
      }),
    restore: (projectId) =>
      request(`/projects/${encodeURIComponent(projectId)}/restore`, {
        method: "POST",
      }),
    permanentDelete: (projectId) =>
      request(`/projects/${encodeURIComponent(projectId)}/permanent`, {
        method: "DELETE",
      }),
    download: async (projectId, fallbackName) => {
      await downloadBlob(
        `${getApiBase()}/projects/${encodeURIComponent(projectId)}/download`,
        fallbackName || "project.zip",
      );
    },
    permissions: {
      list: (projectId) =>
        request(`/projects/${encodeURIComponent(projectId)}/permissions`),
      add: (projectId, username, role) =>
        request(`/projects/${encodeURIComponent(projectId)}/permissions`, {
          method: "POST",
          body: JSON.stringify({ username, role }),
        }),
      update: (projectId, collabUserId, role) =>
        request(
          `/projects/${encodeURIComponent(projectId)}/permissions/${encodeURIComponent(collabUserId)}`,
          {
            method: "PUT",
            body: JSON.stringify({ role }),
          },
        ),
      remove: (projectId, collabUserId) =>
        request(
          `/projects/${encodeURIComponent(projectId)}/permissions/${encodeURIComponent(collabUserId)}`,
          { method: "DELETE" },
        ),
    },
    transfer: (projectId, username, keepAccess) =>
      request(`/projects/${encodeURIComponent(projectId)}/transfer`, {
        method: "POST",
        body: JSON.stringify({ username, keep_access: !!keepAccess }),
      }),
    /**
     * 分片上传会话（v2 P5）：大文件断点续传。
     * init 建会话（manifest + 配额预检）→ 逐片 PUT（X-Part-MD5）→
     * status 位图对位续传 → complete 拼装建档 → abort 中止清理。
     */
    uploadSessions: {
      init: ({ name, kind, totalBytes, files }) =>
        request("/projects/upload-sessions", {
          method: "POST",
          body: JSON.stringify({
            name,
            kind,
            total_bytes: totalBytes,
            files,
          }),
        }),
      putPart: async (sid, fileIndex, chunkIndex, blob, partMd5, signal) => {
        let res;
        try {
          res = await fetch(
            `${getApiBase()}/projects/upload-sessions/${encodeURIComponent(sid)}/parts/${fileIndex}/${chunkIndex}`,
            {
              method: "PUT",
              headers: authHeaders({ "X-Part-MD5": partMd5 }),
              body: blob,
              credentials: "include",
              signal,
            },
          );
        } catch (e) {
          if (e?.name === "AbortError") throw e;
          throw new Error(translateError(e.message || "Request timed out"));
        }
        if (!res.ok) {
          await parseErrorResponse(res);
        }
        return res.json();
      },
      status: (sid, options = {}) =>
        request(
          `/projects/upload-sessions/${encodeURIComponent(sid)}`,
          options,
        ),
      complete: (sid) =>
        request(
          `/projects/upload-sessions/${encodeURIComponent(sid)}/complete`,
          {
            method: "POST",
          },
        ),
      abort: (sid) =>
        request(`/projects/upload-sessions/${encodeURIComponent(sid)}`, {
          method: "DELETE",
        }),
    },
  },
};
