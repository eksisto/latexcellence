import { useCallback, useRef, useState } from "react";
import { toast } from "sonner";

import useEditorStore, { activeFilePath, findTab } from "@/store/editorStore";
import { api } from "@/api/index";
import { translateError } from "@/utils/errorMessages";
import tabViewRegistry from "@/lib/editor/tabViews";
import {
  applyFormatToState,
  applyFormatToView,
  planFormatApply,
  resolveFormatTargetPath,
} from "@/lib/editor/formatApply";

/**
 * 一键格式化（editor-productivity-design §5.3 阶段五 / 决策 Q8）。
 *
 * 点击后调 POST /api/project/format-latex 纯转换端点（携带**发起时**的
 * tab 缓冲区全文），成功后按 planFormatApply 决策替换**发起 tab**的
 * 缓冲区并置脏（落盘仍走 save-file 单一路径 / 自动保存）+ toast 提示
 * Ctrl+Z 可撤销；不做 diff 预览。
 *
 * 并发守卫：请求在途时拒新请求（按钮同步禁用）；请求期间活跃 tab 被
 * 切走时不切视图，只替换发起 tab 的缓冲区数据（按 filePath 定位）；
 * 发起 tab 被关闭则静默丢弃结果。
 *
 * Q8 未要求内容一致性校验：返回后直接应用最新结果——请求在途期间用户
 * 继续编辑的少量改动会被格式化结果覆盖，Ctrl+Z 一步可回（决策已记录）。
 *
 * @param {{workspaceId: string, t: Function, getView: () => object|null}} params
 *   getView 返回页面持有的 CM6 视图实例（EditorPage editorViewRef）。
 */
export default function useEditorFormat({ workspaceId, t, getView }) {
  const [formatting, setFormatting] = useState(false);
  const formattingRef = useRef(false);

  /**
   * @param {string} [filePath] 目标 tab（缺省 = 激活 tab；非字符串实参
   *   ——如工具栏误传的点击事件对象——经 resolveFormatTargetPath 归一化
   *   回落到激活 tab，防止被当作真值路径使用）
   * @returns {Promise<boolean>} 是否成功应用（含"无需变更"提示）
   */
  const handleFormat = useCallback(
    async (filePath) => {
      if (formattingRef.current) return false;
      const snapshot = useEditorStore.getState();
      const targetPath = resolveFormatTargetPath(
        filePath,
        activeFilePath(snapshot),
      );
      const tab = targetPath ? findTab(snapshot, targetPath) : null;
      if (!targetPath || !tab) return false;

      formattingRef.current = true;
      setFormatting(true);
      try {
        const res = await api.project.formatLatex(tab.buffer, workspaceId);
        // 响应返回时刻重新定位发起 tab：期间可能被切走 / 编辑 / 关闭
        const cur = useEditorStore.getState();
        const target = findTab(cur, targetPath);
        const view = getView ? getView() : null;
        const decision = planFormatApply({
          targetPath,
          activePath: cur.activeTabPath,
          viewDoc: view && !view.destroyed ? view.state.doc.toString() : null,
          archivedDoc: (() => {
            const state = tabViewRegistry.getState(targetPath);
            return state ? state.doc.toString() : null;
          })(),
          targetBuffer: target ? target.buffer : null,
          formatted: res.content,
        });

        if (decision === "skip_missing") return false;
        if (decision === "skip_unchanged") {
          toast.info(t("components:editor.formatUnchanged"));
          return true;
        }

        if (decision === "view") {
          // 激活 tab：对视图单事务整文档替换 → onChange 回写 store 置脏，
          // CM6 history 记录一次变更，Ctrl+Z 一步撤销回原文
          applyFormatToView(view, res.content);
        } else if (decision === "state") {
          // 后台 tab（存档一致）：对 EditorState 存档单事务 update，
          // 历史随存档保留；store 缓冲区同步写回
          const archived = tabViewRegistry.getState(targetPath);
          const next = applyFormatToState(archived, res.content);
          tabViewRegistry.setState(
            targetPath,
            next,
            tabViewRegistry.getConfig(targetPath) ?? {},
          );
          cur.setTabBuffer(targetPath, res.content);
        } else {
          // 后台 tab（无存档 / 失配降级）：仅写回 store，换入时按缓冲区
          // 重建（历史复位，可接受降级，见 formatApply.js 模块注释）
          cur.setTabBuffer(targetPath, res.content);
        }
        toast.success(t("components:editor.formatApplied"));
        return true;
      } catch (e) {
        toast.error(t("components:editor.formatFailed"), {
          description: translateError({ error: e?.code, message: e?.message }),
        });
        return false;
      } finally {
        formattingRef.current = false;
        setFormatting(false);
      }
    },
    [workspaceId, t, getView],
  );

  return { formatting, handleFormat };
}
