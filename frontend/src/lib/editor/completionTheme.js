/**
 * 行内补全弹层主题（completionTheme）。
 *
 * 默认 CM 补全 UI 是一张无圆角的白底细边矩形条，与本站浮层语言脱节。
 * 这里把它对齐到全站共享的浮层词汇（--surface-radius / --surface-shadow /
 * --card / --border，与 Sonner toast 同源），并把每一行排成学术书籍索引页
 * 的样式：等宽命令名 —— 引导点（dot leaders）—— 右对齐楷体中文释义，
 * 与命令面板「\命令 + 中文名」的行构成同一数据库两个入口的视觉呼应。
 *
 * 实现约束：
 *   - 全部颜色走 CSS 变量，亮/暗主题切换零 JS 参与；
 *   - 字号为固定 px，不随缓冲区字号偏好缩放，保持弹层密度稳定；
 *   - 选择器均带 .cm-tooltip 前缀压过 CM base theme 的默认规则
 *     （含 &light/&dark 下 li[aria-selected] 的蓝底白字）；
 *   - 激活项以 aria-selected 属性标记（CM 以属性而非 class 标记选中行）；
 *   - icons:false 下行内只有 label/detail 两个 span，引导点由
 *     label::after 伪元素充当 flex 子项实现（空盒基线 = 盒下缘，
 *     点线恰好落在行基线上，即索引页排版的原生做法）。
 */

import { EditorView } from "@codemirror/view";

export const completionPopupTheme = EditorView.theme({
  /* ── 浮层容器：toast 同款卡片外观 ─────────────────────────────────── */
  ".cm-tooltip.cm-tooltip-autocomplete": {
    border: "1px solid var(--border)",
    borderRadius: "var(--surface-radius)",
    backgroundColor: "var(--card)",
    boxShadow: "var(--surface-shadow)",
    padding: "4px",
    overflow: "hidden",
  },

  /* ── 列表：收窄宽度（本库条目短），高度上限略放宽 ─────────────────── */
  ".cm-tooltip.cm-tooltip-autocomplete > ul": {
    fontFamily:
      "var(--font-mono), ui-monospace, SFMono-Regular, Menlo, Consolas, monospace",
    maxWidth: "min(420px, 92vw)",
    minWidth: "240px",
    maxHeight: "16em",
    padding: "0",
  },

  /* ── 行：圆角药丸 + 基线对齐的三段式（命令名 ······ 中文释义） ────── */
  ".cm-tooltip.cm-tooltip-autocomplete > ul > li": {
    display: "flex",
    alignItems: "baseline",
    margin: "1px 2px",
    padding: "4px 10px",
    borderRadius: "var(--radius-sm)",
    lineHeight: "1.4",
    color: "var(--foreground)",
    cursor: "pointer",
  },
  ".cm-tooltip.cm-tooltip-autocomplete > ul > li[aria-selected]": {
    backgroundColor: "var(--accent)",
    color: "var(--accent-foreground)",
  },

  /* 命令名：固定字号，不随缓冲区字号偏好抖动 */
  ".cm-tooltip.cm-tooltip-autocomplete .cm-completionLabel": {
    flexShrink: "0",
    fontSize: "13px",
    fontWeight: "500",
  },

  /* 命中子串高亮：去默认下划线，仅加粗示意 */
  ".cm-tooltip.cm-tooltip-autocomplete .cm-completionMatchedText": {
    color: "inherit",
    fontWeight: "600",
    textDecoration: "none",
  },

  /* 引导点：label::after 作为弹性子项撑开剩余空间，点线落基线 */
  ".cm-tooltip.cm-tooltip-autocomplete .cm-completionLabel::after": {
    content: '""',
    flex: "1 1 12px",
    minWidth: "12px",
    marginRight: "8px",
    borderBottom:
      "1px dotted color-mix(in oklab, var(--muted-foreground) 50%, transparent)",
  },

  /* 中文释义：楷体右对齐，弱化但不失读 */
  ".cm-tooltip.cm-tooltip-autocomplete .cm-completionDetail": {
    fontFamily: "var(--font-sans)",
    fontSize: "11px",
    color: "var(--muted-foreground)",
    textAlign: "right",
    overflow: "hidden",
    textOverflow: "ellipsis",
  },
  ".cm-tooltip.cm-tooltip-autocomplete > ul > li[aria-selected] .cm-completionDetail":
    {
      color: "color-mix(in oklab, var(--accent-foreground) 72%, transparent)",
    },
});
