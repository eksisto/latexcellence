/* ============================================================
 * LateXcellence · Showcase — 主题单一事实来源
 *
 * 顺序必须与 frontend/src/theme/useThemeManager.js 的 THEME_ORDER 一致；
 * swatch / accent 取自已生成 css/themes.css 的主题 token：
 *   swatch  = 各主题 light 档 --primary
 *   accent  = 各主题 light 档 --accent
 * css/themes.css 由 tools/gen-themes.mjs 从 frontend/src/theme/*.css 生成，
 * 修改主题请在 frontend 改完后再跑 `npm run themes:gen`。
 * ============================================================ */

export const THEMES = [
  {
    id: "yingtaohong",
    name: "樱桃红",
    sub: "Pink",
    swatch: "oklch(0.53 0.24 17)",
    accent: "oklch(0.94 0.035 17)",
  },
  {
    id: "hailuocheng",
    name: "海螺橙",
    sub: "Orange",
    swatch: "oklch(0.58 0.19 36)",
    accent: "oklch(0.94 0.035 36)",
  },
  {
    id: "hupohuang",
    name: "琥珀黄",
    sub: "Yellow",
    swatch: "oklch(0.75 0.16 80)",
    accent: "oklch(0.94 0.035 80)",
  },
  {
    id: "tingwulv",
    name: "庭芜绿",
    sub: "Green",
    swatch: "oklch(0.55 0.15 150)",
    accent: "oklch(0.94 0.035 150)",
  },
  {
    id: "jingtailan",
    name: "景泰蓝",
    sub: "Blue",
    swatch: "oklch(0.56 0.18 246)",
    accent: "oklch(0.94 0.04 246)",
  },
  {
    id: "ningyezi",
    name: "凝夜紫",
    sub: "Purple",
    swatch: "oklch(0.52 0.25 287)",
    accent: "oklch(0.94 0.035 287)",
  },
  {
    id: "youminghei",
    name: "幽冥黑",
    sub: "Black",
    swatch: "oklch(0.18 0 0)",
    accent: "oklch(0.94 0 0)",
  },
];

export const DEFAULT_THEME = "jingtailan";

/* 主题卡片的渐变色卡：白 → 浅 accent → 主色，与原有样式保持一致 */
export function themeGradient(t) {
  return `linear-gradient(135deg, oklch(0.985 0 0) 0 45%, ${t.accent} 45% 72%, ${t.swatch} 72% 100%)`;
}
