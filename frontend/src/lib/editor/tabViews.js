/**
 * per-tab CM6 EditorState / 滚动位置登记表（editor-productivity-plan §4，阶段一）。
 *
 * 单 CM6 实例 + 每文件独立 EditorState 交换：tab 切换时把当前视图的
 * EditorState（含撤销历史与选区）按 filePath 收起、换入目标 tab 的存档；
 * 滚动位置不属于 EditorState，由本登记表单独记忆并在换回时恢复。
 *
 * 本模块只做「key → 对象」的通用登记（不感知 CM6 类型），纯数据结构可被
 * node --test 直接单测；EditorState 对象本身由 LaTeXEditor 存取。
 */

/**
 * @param {Map<string, {state: unknown, config: object}>} [states] 注入初始映射（测试用）
 * @param {Map<string, number>} [scrolls]
 */
export function createTabViewRegistry(states = new Map(), scrolls = new Map()) {
  return {
    /** 取 tab 的 EditorState 存档（未登记返回 undefined） */
    getState(path) {
      return states.get(path)?.state;
    },
    /** 取 tab 存档时的外观配置（softWrap / readOnly，用于换入时失配检测） */
    getConfig(path) {
      return states.get(path)?.config ?? null;
    },
    /**
     * 登记 / 覆盖 tab 的 EditorState 存档。
     * @param {string} path
     * @param {unknown} state EditorState（不校验类型，调用方保证）
     * @param {object} [config] 存档时的外观配置快照
     */
    setState(path, state, config = {}) {
      states.set(path, { state, config });
    },
    getScroll(path) {
      return scrolls.get(path) ?? 0;
    },
    setScroll(path, top) {
      scrolls.set(path, top);
    },
    /** tab 关闭 / 缓冲区被外部整体替换时丢弃存档（下次换入按 store 缓冲区重建） */
    drop(path) {
      states.delete(path);
      scrolls.delete(path);
    },
    /** 项目切换守卫：清空全部登记（防止上一个项目的历史串入新项目） */
    clear() {
      states.clear();
      scrolls.clear();
    },
  };
}

/** 页面级单例：/editor 会话期内共享（页面卸载即随模块回收） */
const tabViewRegistry = createTabViewRegistry();

export default tabViewRegistry;
