/**
 * FormulaPreviewCard —— 当前公式固定预览卡（editor-experience-design
 * §4.4 / 决策 Q11，阶段四）。
 *
 * 编辑区底部、状态栏上方的悬浮卡片（不进右栏 tab——右栏已被编译/助手
 * 占用）：订阅 stats hub 光标通道，尾随防抖约 400ms 后取光标所在公式，
 * 用 findFormulaAt 识别、loadKatex 单例渲染。
 *
 * 与悬停浮层（formulaHover.js）的策略差异：固定卡常驻编辑区下方，
 * 「无公式」「公式非法渲染失败」时**静默隐藏**——常驻场景下错误提示
 * 反而构成噪声，故不显示渲染失败文案（悬停浮层是即时唤起的一次性
 * 浮层，失败需要反馈）。
 *
 * hub 契约：监听器无参调用，快照须经 getSnapshot() 回读（与
 * EditorStatusBar / SyncTeX 正向同步同源约定）；快照只进 ref，光标移动
 * 不触发本组件以外的重渲染。过期渲染（防抖期间光标又移动 / 换档）按
 * 递增序号丢弃，绝不回写旧公式。
 */

import { useEffect, useRef, useState } from "react";

import useEditorStore, { activeBuffer } from "@/store/editorStore";
import { findFormulaAt, lineColToOffset } from "@/lib/editor/formulaHoverScan";
import { KATEX_RENDER_OPTIONS, loadKatex } from "@/lib/editor/formulaHover";

/** 公式跟随防抖阈值（Q11：约 400ms 停顿后渲染光标所在公式） */
export const FORMULA_CARD_DEBOUNCE_MS = 400;

/**
 * 当前公式固定预览卡。
 * @param {object} props
 * @param {import("@/lib/editor/stats").createStatsHub} props.hub stats hub
 *   （光标通道，LaTeXEditor 发布 / 状态栏订阅的同一实例）
 */
export default function FormulaPreviewCard({ hub }) {
  const [html, setHtml] = useState(null);
  const seqRef = useRef(0);
  const timerRef = useRef(null);
  const cursorRef = useRef(null);

  useEffect(() => {
    const evaluate = async () => {
      const seq = seqRef.current + 1;
      seqRef.current = seq;
      const cur = useEditorStore.getState();
      const cursor = cursorRef.current;
      const text = cur.activeTabPath ? activeBuffer(cur) : "";
      const pos = cursor ? lineColToOffset(text, cursor.line, cursor.col) : -1;
      const formula = pos >= 0 ? findFormulaAt(text, pos) : null;
      if (!formula) {
        if (seq === seqRef.current) setHtml(null);
        return;
      }
      try {
        const katex = await loadKatex();
        if (seq !== seqRef.current) return; // 防抖期间光标又移动：过期丢弃
        const next = katex.renderToString(formula.text, {
          ...KATEX_RENDER_OPTIONS,
          displayMode: formula.display,
        });
        if (seq === seqRef.current) setHtml(next);
      } catch (_) {
        // KaTeX parseError / katex 块加载失败：静默隐藏（Q11 固定卡策略）
        if (seq === seqRef.current) setHtml(null);
      }
    };
    const schedule = () => {
      // hub 契约：监听器无参调用，快照须经 getSnapshot() 回读
      cursorRef.current = hub.getSnapshot();
      if (timerRef.current !== null) {
        window.clearTimeout(timerRef.current);
      }
      timerRef.current = window.setTimeout(() => {
        timerRef.current = null;
        void evaluate();
      }, FORMULA_CARD_DEBOUNCE_MS);
    };
    const unsubscribe = hub.subscribe(schedule);
    // 换档即弃：activeTabPath 变化时清定时器、作废在途渲染并立即隐藏，
    // 防止旧 tab 的公式残留到新 tab（LaTeXEditor 换档 effect 会为新 tab
    // 重发 stats；若新快照与旧快照 statsEqual 全等被 hub 去重不发布，
    // 卡片保持隐藏而非残留旧公式，光标一动即自愈）
    const unsubTabSwitch = useEditorStore.subscribe((s, prev) => {
      if (s.activeTabPath === prev.activeTabPath) return;
      if (timerRef.current !== null) {
        window.clearTimeout(timerRef.current);
        timerRef.current = null;
      }
      seqRef.current += 1;
      setHtml(null);
    });
    return () => {
      unsubscribe();
      unsubTabSwitch();
      if (timerRef.current !== null) {
        window.clearTimeout(timerRef.current);
        timerRef.current = null;
      }
      seqRef.current += 1; // 在途渲染过期，卸载后不回写 state
    };
  }, [hub]);

  if (!html) return null;
  return (
    <div
      className="absolute bottom-2 left-2 z-[5] max-h-40 max-w-[min(560px,calc(100%-1rem))] overflow-auto rounded-lg border border-border bg-card/95 px-3 py-2 shadow-[var(--surface-shadow)] backdrop-blur-sm [&_.katex-display]:m-0"
      role="img"
      aria-label="formula-preview"
      data-testid="formula-preview-card"
      // renderToString 固定 trust:false（禁止 \href 写入任意属性），
      // 输出为 KaTeX 生成的标记，无原始用户 HTML（与悬停浮层同约束）
      dangerouslySetInnerHTML={{ __html: html }}
    />
  );
}
