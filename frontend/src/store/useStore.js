/**
 * 全局状态管理（Zustand）
 *
 * 管理以下全局状态：
 *   - projectPath / fileTree / activeFile：当前打开的 LaTeX 项目信息
 *   - typos / expressions：错字查找和表达优化的结果列表
 *   - pdfUrl：编译后的 PDF 预览地址
 *   - loadingStates：各模块的加载状态
 *
 * 使用 Zustand 而非 React Context，原因是：
 *   1. 无需 Provider 包裹，任意组件可直接 import
 *   2. 选择器模式避免不必要的重渲染
 *   3. 零样板代码
 */
import { create } from "zustand";

const useStore = create((set) => ({
  /* ===== 项目状态 ===== */
  projectPath: null,       // 当前打开的项目目录路径
  fileTree: [],            // 文件树节点列表（FileNode 结构）
  activeFile: null,        // 当前选中的 .tex 文件路径

  /* ===== 功能模块结果 ===== */
  typos: [],               // 错字检查结果（逐段落）
  expressions: [],         // 表达优化结果（逐段落）

  /* ===== PDF 预览 ===== */
  pdfUrl: null,            // 编译后的 PDF 文件 URL

  /* ===== 各模块加载状态 ===== */
  loadingStates: {
    typoCheck: false,
    expressionOpt: false,
  },

  /* ===== setter 方法 ===== */
  setProjectPath: (path) => set({ projectPath: path }),
  setFileTree: (tree) => set({ fileTree: tree }),
  setActiveFile: (file) => set({ activeFile: file }),
  setTypos: (typos) => set({ typos }),
  setExpressions: (expressions) => set({ expressions }),
  setPdfUrl: (url) => set({ pdfUrl: url }),

  /**
   * 设置单个加载状态
   * @param {string} key   - loadingStates 的 key（如 "typoCheck"）
   * @param {boolean} value - true 表示正在加载
   */
  setLoading: (key, value) =>
    set((state) => ({
      loadingStates: { ...state.loadingStates, [key]: value },
    })),
}));

export default useStore;
