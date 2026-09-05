/**
 * SyncTeX 正反向坐标换算纯模块（editor v3 阶段一 / 决策 Q1+Q5）。
 *
 * 坐标系约定：
 *   - synctex view/edit 使用 72dpi 点（pt）单位、页面左上原点；
 *   - pdf.js viewport 是 CSS 像素、左上原点（rotation 0 时随 scale
 *     线性映射 pt）；convertToPdfPoint 返回 PDF 点、左下原点。
 *
 * 本模块只做无量纲算术，供 node --test 直接单测；DOM / pdf.js 对象
 * 一律留在组件层（PdfJsPreview.jsx）。
 */

/** 缩放边界：50%（长文档縮览）~ 400%（校样细看） */
export const SCALE_MIN = 0.5;
export const SCALE_MAX = 4;

/** 每次 ± 缩放的乘法步长（观感线性，比例恒定） */
export const SCALE_STEP = 1.2;

/**
 * 限幅缩放值；NaN / 非数值输入回落 100%，Infinity 参与限幅。
 * @param {number} value
 * @returns {number} [SCALE_MIN, SCALE_MAX] 内、保留两位小数
 */
export function clampScale(value) {
  const n = Number(value);
  if (Number.isNaN(n)) return 1;
  const clamped = Math.min(SCALE_MAX, Math.max(SCALE_MIN, n));
  return Math.round(clamped * 100) / 100;
}

/**
 * 步进缩放：direction > 0 放大、< 0 缩小。
 * @param {number} scale 当前缩放
 * @param {number} direction
 * @returns {number} 新缩放（限幅、两位小数，避免连乘浮点漂移）
 */
export function stepScale(scale, direction) {
  const factor = direction >= 0 ? SCALE_STEP : 1 / SCALE_STEP;
  return clampScale(Math.round(Number(scale) * factor * 1e6) / 1e6);
}

/**
 * 适宽缩放：让页面宽度恰好填进容器（预留左右留白）。
 * @param {number} containerWidth 容器 CSS 宽度（px）
 * @param {number} pageWidthPt 页面宽度（pt，scale=1 时 viewport 宽）
 * @returns {number} 缩放值（限幅；容器不可测或页面异常时回落 1）
 */
export function fitWidthScale(containerWidth, pageWidthPt) {
  if (!(containerWidth > 50) || !(pageWidthPt > 0)) return 1;
  return clampScale((containerWidth - 24) / pageWidthPt);
}

/**
 * 展示用矩形兜底：垂直盒等记录 w/h 可为 0，直接画会不可见；
 * 给最小可点击的视觉尺寸（pt）。
 * @param {{x:number,y:number,w:number,h:number}} record
 * @returns {{x:number,y:number,w:number,h:number}}
 */
export function rectForDisplay(record) {
  return {
    x: Number(record?.x) || 0,
    y: Number(record?.y) || 0,
    w: Number(record?.w) > 0 ? Number(record.w) : 8,
    h: Number(record?.h) > 0 ? Number(record.h) : 14,
  };
}

/**
 * synctex 矩形（pt、左上原点）→ 页面内 CSS 像素矩形（rotation 0）。
 * @param {{x:number,y:number,w:number,h:number}} record pt 坐标
 * @param {number} scale 当前缩放（1 = 1pt:1px）
 * @returns {{left:number,top:number,width:number,height:number}}
 */
export function synctexRectToCssRect(record, scale) {
  return {
    left: (Number(record?.x) || 0) * scale,
    top: (Number(record?.y) || 0) * scale,
    width: Math.max(0, Number(record?.w) || 0) * scale,
    height: Math.max(0, Number(record?.h) || 0) * scale,
  };
}

/**
 * pdf.js `viewport.convertToPdfPoint` 结果（PDF 点、左下原点）→
 * synctex 左上原点 y（决策 Q5：y = pageHeight − y）。
 * @param {number} pdfY PDF 点纵坐标（原点左下）
 * @param {number} pageHeightPt 页面高度（pt）
 * @returns {number} synctex 纵坐标（原点左上）
 */
export function flipToSynctexY(pdfY, pageHeightPt) {
  return Number(pageHeightPt) - Number(pdfY);
}
