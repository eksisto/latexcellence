/**
 * 全局 UI 状态（非持久化）：当前承载设置对话框的程序化开关与定位分区。
 *
 * ModelBadge 等深层组件需要「点击打开设置对话框并定位到模型管理分区」
 * （multi-model-plan §7.2），深层传递违反 prop drilling 禁令，故经独立
 * 的轻量 store 传递；SettingsDialog 订阅本 store 渲染，工具栏常规入口
 * 与程序化入口共用同一开关。
 */

import { create } from "zustand";

/** 设置对话框支持的定位分区标识：模型管理分区（multi-model-plan §7.1） */
export const SETTINGS_SECTION_MODELS = "models";

const useUiStore = create((set) => ({
  settingsDialog: { open: false, section: null },
  // 模型路由数据版本号：设置保存成功后自增，ModelBadge 据此重新拉取
  // /api/llm/routes（徽标仅在挂载与版本变化时拉取，改派模型后即时刷新）
  modelRoutesVersion: 0,
  bumpModelRoutesVersion: () =>
    set((state) => ({ modelRoutesVersion: state.modelRoutesVersion + 1 })),
  /** 打开设置对话框；section 传入分区标识时渲染后滚动定位到该分区 */
  openSettingsDialog: (section = null) =>
    set({ settingsDialog: { open: true, section } }),
  /** 受控开关（Dialog onOpenChange 直连）；关闭时顺带清除未消费的定位标记 */
  setSettingsDialogOpen: (open) =>
    set((state) => ({
      settingsDialog: {
        open: !!open,
        section: open ? state.settingsDialog.section : null,
      },
    })),
  /** 消费定位分区（滚动定位完成后调用，避免之后常规打开重复定位） */
  clearSettingsSection: () =>
    set((state) => ({
      settingsDialog: { ...state.settingsDialog, section: null },
    })),
}));

export default useUiStore;
