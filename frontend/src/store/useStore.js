/**
 * 全局状态管理（Zustand + persist 持久化）
 *
 * 持久化策略：
 *     - 仅持久化项目身份信息（projectPath、rootPath、activeFile、mainFile、workspaceId）
 *     - 不持久化段落数据、检查结果等临时数据
 *     - 刷新页面后自动恢复项目状态
 *     - workspaceId 用于多浏览器多工作区隔离
 */

/**
 * 获取或生成当前浏览器的工作区唯一标识符。
 * 每个浏览器生成独立的 workspaceId（通过 localStorage 隔离），
 * 多浏览器可同时处理不同工作区而互不干扰。
 */
function getOrCreateWorkspaceId() {
  const key = "latexcellence-workspace-id";
  let id = null;
  try {
    id = localStorage.getItem(key);
  } catch (_) {
    // 忽略
  }
  if (!id) {
    id = crypto.randomUUID
      ? crypto.randomUUID()
      : "ws-" +
        Date.now().toString(36) +
        "-" +
        Math.random().toString(36).slice(2, 10);
    try {
      localStorage.setItem(key, id);
    } catch (_) {
      // 忽略
    }
  }
  return id;
}

import { create } from "zustand";
import { persist } from "zustand/middleware";

/**
 * 项目相关状态的重置切片：登出（clearProject）与会话失效（handleUnauthorized）
 * 共用，保证两条路径清空口径一致——401 后同浏览器换账号登录不得看到前一
 * 账号的检查结果、写作会话等持久化数据（跨账号数据残留防护）。
 * 不含 workspaceId / currentTheme（设备级状态，与账号无关）。
 */
function projectStateReset() {
  return {
    projectPath: null,
    rootPath: null,
    projectId: null,
    projectName: null,
    projectRole: null,
    fileTree: [],
    activeFile: null,
    mainFile: null,
    typoResults: [],
    typoParagraphs: [],
    expressionResults: [],
    expressionParagraphs: [],
    codeReviewResults: [],
    codeReviewParagraphs: [],
    terminologyResults: [],
    terminologyParagraphs: [],
    paragraphTranslateResults: [],
    paragraphTranslateParagraphs: [],
    writingChatSessions: [],
    writingChatCurrentSessionId: null,
    writingChatMessages: [],
    writingChatCompressed: false,
    styleModifySessionId: null,
    styleModifyRevision: null,
    styleModifyOperationId: null,
    checkProgress: null,
  };
}

const useStore = create(
  persist(
    (set, get) => ({
      /* ===== 水合状态（不持久化） ===== */
      hydrated: false,

      /* ===== 系统状态 ===== */
      enabledFeatures: null,
      setEnabledFeatures: (features) => set({ enabledFeatures: features }),
      currentTheme: "youminghei",
      setCurrentTheme: (theme) => set({ currentTheme: theme }),

      /* ===== 多用户认证（P5，不持久化——会话 Cookie 为准，启动时经 /api/auth/me 恢复） ===== */
      authStatus: "loading", // loading | authenticated | pending | unauthenticated
      authUser: null, // { id, username, email, role, status }
      quota: null, // { project_quota_bytes, derived_quota_bytes }
      usage: null, // { project_usage_bytes, derived_usage_bytes }
      setAuthStatus: (status) => set({ authStatus: status }),
      setAuthUser: (user) => set({ authUser: user }),
      setAuthMe: (me) =>
        set({
          authUser: me?.user || null,
          quota: me?.quota || null,
          usage: me?.usage || null,
        }),
      // 配额/用量的更新统一走 setAuthMe（/auth/me 一次带回用户+配额+用量），
      // 不单独提供 setQuota/setUsage——孤立 setter 只会制造陈旧数据（审查 M2）
      /**
       * 多用户模式当前项目身份（持久化，刷新后据此恢复）。
       * 权限实时查库可能变化，进入工作区时按需重新同步 projectRole。
       */
      projectId: null,
      projectName: null, // 多用户项目显示名（页头「当前工作区」小字；单用户由路径推导）
      projectRole: null, // owner | editor | viewer
      setProjectId: (id) => set({ projectId: id }),
      setProjectName: (name) => set({ projectName: name || null }),
      setProjectRole: (role) => set({ projectRole: role }),
      setOpenedProject: ({ projectId, projectRole, projectName }) =>
        set({ projectId, projectRole, projectName: projectName || null }),
      /** 会话失效 / 未登录：清空认证状态，并按登出同口径清空项目与检查结果态。
       * 此前只清项目身份字段，persist 的检查结果/写作会话跨 401 残留，
       * 同浏览器换账号登录会看到前一账号的数据（审查 Required#6）。 */
      handleUnauthorized: () =>
        set({
          authStatus: "unauthenticated",
          authUser: null,
          quota: null,
          usage: null,
          ...projectStateReset(),
        }),
      /** 登出（不发起网络请求，仅清空本地状态；调用方负责调用 api.auth.logout） */
      clearAuth: () =>
        set({
          authStatus: "unauthenticated",
          authUser: null,
          quota: null,
          usage: null,
        }),

      /* ===== 工作区标识（持久化） ===== */
      workspaceId: getOrCreateWorkspaceId(),
      setWorkspaceId: (id) => set({ workspaceId: id }),

      /* ===== 项目状态（持久化） ===== */
      projectPath: null,
      rootPath: null,
      fileTree: [],
      activeFile: null,
      mainFile: null,

      /* ===== style_modify Agent（只持久化恢复所需标识） ===== */
      styleModifySessionId: null,
      styleModifyRevision: null,
      styleModifyOperationId: null,
      styleModifyMode: "agent",

      /* ===== 功能模块结果（不持久化） ===== */
      typos: [],
      expressions: [],
      typoParagraphs: [],
      typoResults: [],
      expressionParagraphs: [],
      expressionResults: [],
      codeReviewParagraphs: [],
      codeReviewResults: [],
      terminologyParagraphs: [],
      terminologyResults: [],
      paragraphTranslateParagraphs: [],
      paragraphTranslateResults: [],

      /* ===== PDF 预览（不持久化） ===== */
      pdfUrl: null,

      /* ===== 自动编译（不持久化） ===== */
      compileResult: null,
      compileLoading: false,

      /* ===== 各模块加载状态（不持久化） ===== */
      loadingStates: {
        typoCheck: false,
        expressionOpt: false,
        codeReview: false,
        terminologyCheck: false,
        paragraphTranslate: false,
        writingChat: false,
        bibManager: false,
      },

      /* ===== 逐段检查进度（不持久化） ===== */
      checkProgress: null,

      /* ===== setter 方法 ===== */
      setProjectPath: (path) => set({ projectPath: path }),
      setRootPath: (path) => set({ rootPath: path }),
      setFileTree: (tree) => set({ fileTree: tree }),
      setActiveFile: (file) => set({ activeFile: file }),
      setMainFile: (file) => set({ mainFile: file }),
      setStyleModifySession: ({ sessionId, revision, operationId } = {}) =>
        set({
          styleModifySessionId: sessionId || null,
          styleModifyRevision: revision || null,
          styleModifyOperationId: operationId || null,
        }),
      setStyleModifyMode: (mode) => set({ styleModifyMode: mode }),
      setTypos: (typos) => set({ typos }),
      setExpressions: (expressions) => set({ expressions }),
      setTypoParagraphs: (paras) => {
        if (typeof paras === "function") {
          set((state) => ({ typoParagraphs: paras(state.typoParagraphs) }));
        } else {
          set({ typoParagraphs: paras });
        }
      },
      setTypoResults: (results) => {
        if (typeof results === "function") {
          set((state) => ({ typoResults: results(state.typoResults) }));
        } else {
          set({ typoResults: results });
        }
      },
      setExpressionParagraphs: (paras) => {
        if (typeof paras === "function") {
          set((state) => ({
            expressionParagraphs: paras(state.expressionParagraphs),
          }));
        } else {
          set({ expressionParagraphs: paras });
        }
      },
      setExpressionResults: (results) => {
        if (typeof results === "function") {
          set((state) => ({
            expressionResults: results(state.expressionResults),
          }));
        } else {
          set({ expressionResults: results });
        }
      },
      setCodeReviewParagraphs: (paras) => {
        if (typeof paras === "function") {
          set((state) => ({
            codeReviewParagraphs: paras(state.codeReviewParagraphs),
          }));
        } else {
          set({ codeReviewParagraphs: paras });
        }
      },
      setCodeReviewResults: (results) => {
        if (typeof results === "function") {
          set((state) => ({
            codeReviewResults: results(state.codeReviewResults),
          }));
        } else {
          set({ codeReviewResults: results });
        }
      },
      setTerminologyParagraphs: (paras) => {
        if (typeof paras === "function") {
          set((state) => ({
            terminologyParagraphs: paras(state.terminologyParagraphs),
          }));
        } else {
          set({ terminologyParagraphs: paras });
        }
      },
      setTerminologyResults: (results) => {
        if (typeof results === "function") {
          set((state) => ({
            terminologyResults: results(state.terminologyResults),
          }));
        } else {
          set({ terminologyResults: results });
        }
      },
      setParagraphTranslateParagraphs: (paras) => {
        if (typeof paras === "function") {
          set((state) => ({
            paragraphTranslateParagraphs: paras(
              state.paragraphTranslateParagraphs,
            ),
          }));
        } else {
          set({ paragraphTranslateParagraphs: paras });
        }
      },
      setParagraphTranslateResults: (results) => {
        if (typeof results === "function") {
          set((state) => ({
            paragraphTranslateResults: results(state.paragraphTranslateResults),
          }));
        } else {
          set({ paragraphTranslateResults: results });
        }
      },
      setPdfUrl: (url) => {
        // blob URL 在替换/清空时主动释放，避免 PDF 预览内存泄漏；
        // 释放放在 updater 外，保持 set 函数纯净
        const prev = get().pdfUrl;
        if (prev && prev !== url && prev.startsWith("blob:")) {
          URL.revokeObjectURL(prev);
        }
        set({ pdfUrl: url });
      },
      setCompileResult: (result) => set({ compileResult: result }),
      setCompileLoading: (loading) => set({ compileLoading: loading }),

      /* ===== 写作助手 ===== */
      writingChatSessions: [],
      writingChatCurrentSessionId: null,
      writingChatMessages: [],
      writingChatCompressed: false,
      setWritingChatSessions: (sessions) => {
        if (typeof sessions === "function") {
          set((state) => ({
            writingChatSessions: sessions(state.writingChatSessions),
          }));
        } else {
          set({ writingChatSessions: sessions });
        }
      },
      setWritingChatCurrentSessionId: (id) =>
        set({ writingChatCurrentSessionId: id }),
      setWritingChatMessages: (messages) => {
        if (typeof messages === "function") {
          set((state) => ({
            writingChatMessages: messages(state.writingChatMessages),
          }));
        } else {
          set({ writingChatMessages: messages });
        }
      },
      setWritingChatCompressed: (c) => set({ writingChatCompressed: c }),

      setLoading: (key, value) =>
        set((state) => ({
          loadingStates: { ...state.loadingStates, [key]: value },
        })),

      setCheckProgress: (progress) => set({ checkProgress: progress }),

      clearProject: () => {
        try {
          localStorage.removeItem("latexcellence-project");
        } catch (_) {
          // 忽略
        }
        set(projectStateReset());
      },
    }),
    {
      name: "latexcellence-project",
      // localStorage 结构版本（client-localstorage-schema）：结构变更时 +1 并补 migrate
      version: 2,
      // v0 → v1：多用户模式新增 projectId/projectRole 持久化字段
      // v1 → v2：新增 projectName（页头展示当前工作区）
      migrate: (persistedState) => ({
        ...persistedState,
        projectId: persistedState?.projectId ?? null,
        projectRole: persistedState?.projectRole ?? null,
        projectName: persistedState?.projectName ?? null,
      }),
      onRehydrateStorage: () => (state, error) => {
        if (error) return;
        // zustand v5 对 localStorage 等同步存储会在 create() 内部同步完成水合，
        // 此刻 const useStore 尚未完成初始化（TDZ），直接调用会抛 ReferenceError
        // 且被 persist 静默吞掉（以 error 重入本回调），导致 hydrated 永久为 false、
        // 各页面刷新恢复逻辑失效——必须推迟到当前同步栈结束后再置位。
        queueMicrotask(() => useStore.setState({ hydrated: true }));
      },
      partialize: (state) => ({
        workspaceId: state.workspaceId,
        projectPath: state.projectPath,
        rootPath: state.rootPath,
        projectId: state.projectId,
        projectName: state.projectName,
        projectRole: state.projectRole,
        activeFile: state.activeFile,
        mainFile: state.mainFile,
        styleModifySessionId: state.styleModifySessionId,
        styleModifyRevision: state.styleModifyRevision,
        styleModifyOperationId: state.styleModifyOperationId,
        styleModifyMode: state.styleModifyMode,
        currentTheme: state.currentTheme,
        typoResults: state.typoResults,
        typoParagraphs: state.typoParagraphs,
        expressionResults: state.expressionResults,
        expressionParagraphs: state.expressionParagraphs,
        codeReviewResults: state.codeReviewResults,
        codeReviewParagraphs: state.codeReviewParagraphs,
        terminologyResults: state.terminologyResults,
        terminologyParagraphs: state.terminologyParagraphs,
        paragraphTranslateResults: state.paragraphTranslateResults,
        paragraphTranslateParagraphs: state.paragraphTranslateParagraphs,
        writingChatSessions: state.writingChatSessions,
        writingChatCurrentSessionId: state.writingChatCurrentSessionId,
        writingChatMessages: state.writingChatMessages,
        writingChatCompressed: state.writingChatCompressed,
      }),
    },
  ),
);

export default useStore;
