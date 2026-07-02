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

      /* ===== PDF 预览（不持久化） ===== */
      pdfUrl: null,

      /* ===== 各模块加载状态（不持久化） ===== */
      loadingStates: {
        typoCheck: false,
        expressionOpt: false,
      },

      /* ===== setter 方法 ===== */
      setProjectPath: (path) => set({ projectPath: path }),
      setRootPath: (path) => set({ rootPath: path }),
      setFileTree: (tree) => set({ fileTree: tree }),
      setActiveFile: (file) => set({ activeFile: file }),
      setMainFile: (file) => set({ mainFile: file }),
      setTypos: (typos) => set({ typos }),
      setExpressions: (expressions) => set({ expressions }),
      setTypoParagraphs: (paras) => set({ typoParagraphs: paras }),
      setTypoResults: (results) => set({ typoResults: results }),
      setPdfUrl: (url) => set({ pdfUrl: url }),

      setLoading: (key, value) =>
        set((state) => ({
          loadingStates: { ...state.loadingStates, [key]: value },
        })),

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
      }),
    }
  )
);

export default useStore;
