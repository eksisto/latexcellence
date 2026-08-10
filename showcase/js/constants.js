/* ============================================================
 * LateXcellence · Showcase — 可调常量集中管理
 * 滚动引擎 / 布局断点 / 动效节奏的魔法数字统一收口到这里，
 * 调参时只改本文件，并保持注释说明作用。
 * ============================================================ */

export const LAYOUT = {
  /* 滚轮灵敏度：原生滚轮位移乘以该系数，页面滚动更快（可调） */
  WHEEL_SENSITIVITY: 1,

  /* 槽位滚动：一格滚轮 = 平滑跳到相邻分区锚点（实现见 js/scrolly.js 槽位滚动段）。
     SNAP_MS：跳转动画总时长（ms）；
     SNAP_TOL_PX：判定“已停在某槽位”的容差（px），避免停在锚点附近时方向误判。 */
  SNAP_MS: 1800,
  SNAP_TOL_PX: 24,

  /* 行号栏同时显示的行数（编辑器隐喻的窗口高度） */
  GUTTER_COUNT: 25,
  /* 每行行高（px），用于把滚动位移换算成“当前行号” */
  GUTTER_LINE_H: 30,

  /* 编译角标判定“intro.pdf 已生成”的滚动进度阈值（0~1） */
  PROGRESS_DONE: 0.985,

  /* 锚点跳转：兜底进度（场景未标注 data-anchor 时使用） */
  RAIL_ANCHOR_RATIO: 0.42,

  /* 锚点跳转：各场景模板的锚点进度 —— 取该模板最后一个入场元素完全显示的阈值
     （hero / themes / outro 在 p=0 即完整可见，故为 0） */
  RAIL_ANCHOR_P: {
    hero: 0,
    act: 0.52, // scene-copy 0.28 / scene-stage 0.52
    chapter: 0.52, // scene-copy 0.28 / scene-stage 0.52
    principles: 0.56, // head 0.24 / 卡片 0.32 / 0.44 / 0.56
    stack: 0.56, // head 0.24 / 卡片 0.32 / 0.44 / 0.56
    themes: 0,
    outro: 0,
  },

  /* 主题横向滚动：行程的右端留白（px） */
  THEMES_TRAVEL_PAD: 80,

  /* 单场景超高兜底：内容高度超过视口该余量时标记 .unfit */
  UNFIT_MARGIN: 24,

  /* 小屏 / 短屏断点：低于任一宽度/高度时退化为普通堆叠页 */
  STACK_BREAKPOINT_W: 1024,
  STACK_BREAKPOINT_H: 620,

  /* Hero 打字机节奏（ms） */
  TYPE_CHAR_MS: 26,
  TYPE_LINE_MS: 240,
  TYPE_START_MS: 400,
};
