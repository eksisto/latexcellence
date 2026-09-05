import test from "node:test";
import assert from "node:assert/strict";

import {
  clampScale,
  fitWidthScale,
  flipToSynctexY,
  rectForDisplay,
  SCALE_MAX,
  SCALE_MIN,
  stepScale,
  synctexRectToCssRect,
} from "../src/lib/editor/synctexMath.js";

test("clampScale 限幅并保留两位小数，非法输入回落 1", () => {
  assert.equal(clampScale(1.234), 1.23);
  assert.equal(clampScale(0.1), SCALE_MIN);
  assert.equal(clampScale(99), SCALE_MAX);
  assert.equal(clampScale(Number.NaN), 1);
  assert.equal(clampScale("abc"), 1);
  assert.equal(clampScale(Number.POSITIVE_INFINITY), SCALE_MAX);
});

test("stepScale 乘法步进 + 边界限幅", () => {
  assert.equal(stepScale(1, 1), 1.2);
  assert.equal(stepScale(1.2, -1), 1);
  // 连乘浮点漂移被两位小数取整吸收
  assert.equal(stepScale(stepScale(1, 1), 1), 1.44);
  assert.equal(stepScale(SCALE_MAX, 1), SCALE_MAX);
  assert.equal(stepScale(SCALE_MIN, -1), SCALE_MIN);
});

test("fitWidthScale 容器宽减留白换算缩放，异常输入回落 1", () => {
  // A4 宽 595pt，容器 619px → (619-24)/595 = 1
  assert.equal(fitWidthScale(619, 595), 1);
  // 容器 1214px → (1214-24)/595 ≈ 2
  assert.equal(Math.round(fitWidthScale(1214, 595) * 100) / 100, 2);
  // 容器过窄（未挂载/折叠）与页面异常回落 1
  assert.equal(fitWidthScale(0, 595), 1);
  assert.equal(fitWidthScale(30, 595), 1);
  assert.equal(fitWidthScale(600, 0), 1);
  // 极宽容器被 SCALE_MAX 限幅
  assert.equal(fitWidthScale(100000, 595), SCALE_MAX);
});

test("rectForDisplay 零宽/高兜底为最小可见尺寸（垂直盒记录）", () => {
  assert.deepEqual(rectForDisplay({ x: 10, y: 20, w: 0, h: 0 }), {
    x: 10,
    y: 20,
    w: 8,
    h: 14,
  });
  assert.deepEqual(rectForDisplay({ x: 1, y: 2, w: 3, h: 4 }), {
    x: 1,
    y: 2,
    w: 3,
    h: 4,
  });
  // 缺字段 / 非法值按 0 处理再兜底
  assert.deepEqual(rectForDisplay({}), { x: 0, y: 0, w: 8, h: 14 });
  assert.deepEqual(rectForDisplay(null), { x: 0, y: 0, w: 8, h: 14 });
});

test("synctexRectToCssRect 点单位 → CSS 像素线性换算（左上原点一致）", () => {
  const record = { x: 100, y: 50, w: 200, h: 12 };
  assert.deepEqual(synctexRectToCssRect(record, 1), {
    left: 100,
    top: 50,
    width: 200,
    height: 12,
  });
  assert.deepEqual(synctexRectToCssRect(record, 1.5), {
    left: 150,
    top: 75,
    width: 300,
    height: 18,
  });
  // 非法值收敛为 0 尺寸，不产生 NaN 几何
  assert.deepEqual(synctexRectToCssRect({ x: "a", y: null, w: -3, h: 4 }, 2), {
    left: 0,
    top: 0,
    width: 0,
    height: 8,
  });
});

test("flipToSynctexY 左下原点 → synctex 左上原点（y = pageHeight − y）", () => {
  // A4 高 842pt：页面顶端（PDF y=842）↔ synctex y=0；底端互换
  assert.equal(flipToSynctexY(842, 842), 0);
  assert.equal(flipToSynctexY(0, 842), 842);
  assert.equal(flipToSynctexY(792, 792), 0);
  // 视口中心互为镜像
  assert.equal(flipToSynctexY(421, 842), 421);
});

test("双击取点闭环：CSS 像素 → PDF 点 → synctex 左上原点（Q5 全链路）", () => {
  // scale=1，A4 页（595×842），点击距页面顶端 100px 处：
  // convertToPdfPoint 给出 pdfY = 842 − 100 = 742（左下原点），
  // flipToSynctexY 还原 synctex y = 100（与视口 CSS 像素值一致，互为逆变换）
  const scale = 1;
  const pageHeightPt = 842;
  const cssY = 100;
  const pdfY = pageHeightPt - cssY / scale; // convertToPdfPoint(rotation 0)
  assert.equal(flipToSynctexY(pdfY, pageHeightPt), 100);
  // scale=2：CSS 200px → PDF 点 100 → synctex 100
  const cssY2 = 200;
  const pdfY2 = pageHeightPt - cssY2 / 2;
  assert.equal(flipToSynctexY(pdfY2, pageHeightPt), 100);
});
