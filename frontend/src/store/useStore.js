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

const useStore = create(
  persist(
    (set) => ({
      /* ===== 水合状态（不持久化） ===== */
      hydrated: false,

      /* ===== 系统状态 ===== */
      enabledFeatures: null,
      setEnabledFeatures: (features) => set({ enabledFeatures: features }),
      currentTheme: "youminghei",
      setCurrentTheme: (theme) => set({ currentTheme: theme }),

      /* ===== 工作区标识（持久化） ===== */
      workspaceId: getOrCreateWorkspaceId(),
      setWorkspaceId: (id) => set({ workspaceId: id }),

      /* ===== 项目状态（持久化） ===== */
      projectPath: null,
      rootPath: null,
      fileTree: [],
      activeFile: null,
      mainFile: null,

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
      setPdfUrl: (url) => set({ pdfUrl: url }),
      setCompileResult: (result) => set({ compileResult: result }),
      setCompileLoading: (loading) => set({ compileLoading: loading }),

      /* ===== 写作助手 Chat（仅持久化 currentSessionId） ===== */
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
        set({
          projectPath: null,
          rootPath: null,
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
          writingChatMessages: [],
          writingChatCompressed: false,
          checkProgress: null,
        });
      },
    }),
    {
      name: "latexcellence-project",
      onRehydrateStorage: () => (state, error) => {
        if (!error) {
          useStore.setState({ hydrated: true });
        }
      },
      partialize: (state) => ({
        workspaceId: state.workspaceId,
        projectPath: state.projectPath,
        rootPath: state.rootPath,
        activeFile: state.activeFile,
        mainFile: state.mainFile,
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
        writingChatCurrentSessionId: state.writingChatCurrentSessionId,
      }),
    },
  ),
);

export default useStore;
