/**
 * 全局状态管理（Zustand）
 */
import { create } from "zustand";

const useStore = create((set) => ({
  /* ===== 系统状态 ===== */
  enabledFeatures: null,
  setEnabledFeatures: (features) => set({ enabledFeatures: features }),

  /* ===== 项目状态 ===== */
  projectPath: null,
  rootPath: null,
  fileTree: [],
  activeFile: null,
  mainFile: null,

  /* ===== 功能模块结果 ===== */
  typos: [],
  expressions: [],
  typoParagraphs: [],
  typoResults: [],

  /* ===== PDF 预览 ===== */
  pdfUrl: null,

  /* ===== 各模块加载状态 ===== */
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

  clearProject: () =>
    set({
      projectPath: null,
      rootPath: null,
      fileTree: [],
      activeFile: null,
      mainFile: null,
    }),
}));

export default useStore;
