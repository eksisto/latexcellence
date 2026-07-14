/**
 * 全局状态管理（Zustand + persist 持久化）
 *
 * 持久化策略：
 *     - 仅持久化项目身份信息（projectPath、rootPath、activeFile、mainFile）
 *     - 不持久化段落数据、检查结果等临时数据
 *     - 刷新页面后自动恢复项目状态
 */
import { create } from "zustand";
import { persist } from "zustand/middleware";

const useStore = create(
  persist(
    (set) => ({
      /* ===== 系统状态 ===== */
      enabledFeatures: null,
      setEnabledFeatures: (features) => set({ enabledFeatures: features }),

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
      formattingParagraphs: [],
      formattingResults: [],
      terminologyParagraphs: [],
      terminologyResults: [],

      /* ===== PDF 预览（不持久化） ===== */
      pdfUrl: null,

      /* ===== 自动编译（不持久化） ===== */
      compileResult: null,
      compileLoading: false,

      /* ===== 各模块加载状态（不持久化） ===== */
      loadingStates: {
        typoCheck: false,
        expressionOpt: false,
        formatting: false,
        terminologyCheck: false,
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
          set((state) => ({ expressionParagraphs: paras(state.expressionParagraphs) }));
        } else {
          set({ expressionParagraphs: paras });
        }
      },
      setExpressionResults: (results) => {
        if (typeof results === "function") {
          set((state) => ({ expressionResults: results(state.expressionResults) }));
        } else {
          set({ expressionResults: results });
        }
      },
      setFormattingParagraphs: (paras) => {
        if (typeof paras === "function") {
          set((state) => ({ formattingParagraphs: paras(state.formattingParagraphs) }));
        } else {
          set({ formattingParagraphs: paras });
        }
      },
      setFormattingResults: (results) => {
        if (typeof results === "function") {
          set((state) => ({ formattingResults: results(state.formattingResults) }));
        } else {
          set({ formattingResults: results });
        }
      },
      setTerminologyParagraphs: (paras) => {
        if (typeof paras === "function") {
          set((state) => ({ terminologyParagraphs: paras(state.terminologyParagraphs) }));
        } else {
          set({ terminologyParagraphs: paras });
        }
      },
      setTerminologyResults: (results) => {
        if (typeof results === "function") {
          set((state) => ({ terminologyResults: results(state.terminologyResults) }));
        } else {
          set({ terminologyResults: results });
        }
      },
      setPdfUrl: (url) => set({ pdfUrl: url }),
      setCompileResult: (result) => set({ compileResult: result }),
      setCompileLoading: (loading) => set({ compileLoading: loading }),

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
          formattingResults: [],
          formattingParagraphs: [],
          terminologyResults: [],
          terminologyParagraphs: [],
          checkProgress: null,
        });
      },
    }),
    {
      name: "latexcellence-project",
      partialize: (state) => ({
        projectPath: state.projectPath,
        rootPath: state.rootPath,
        activeFile: state.activeFile,
        mainFile: state.mainFile,
        typoResults: state.typoResults,
        typoParagraphs: state.typoParagraphs,
        expressionResults: state.expressionResults,
        expressionParagraphs: state.expressionParagraphs,
        formattingResults: state.formattingResults,
        formattingParagraphs: state.formattingParagraphs,
        terminologyResults: state.terminologyResults,
        terminologyParagraphs: state.terminologyParagraphs,
      }),
    }
  )
);

export default useStore;
