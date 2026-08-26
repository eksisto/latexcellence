/**
 * commandZh 单元测试：命令中文名展示的界面语言本地化。
 *   - en 等非中文界面 → null（调用方隐藏）；
 *   - zh → 原样（检索数据即简体，不做无谓转换）；
 *   - zh-TW → 经 OpenCC 简→繁转换；转换 chunk 加载失败回退原样。
 */
import test from "node:test";
import assert from "node:assert/strict";

import {
  commandZhIntent,
  getCommandZhDisplay,
} from "../src/lib/editor/commandZh.js";

test("intent：en 隐藏、zh 原样、zh-TW 转繁；大小写与区域码容错", () => {
  assert.equal(commandZhIntent("en"), "hidden");
  assert.equal(commandZhIntent("zh"), "simplified");
  assert.equal(commandZhIntent("zh-TW"), "traditional");
  // normalizeLanguageCode 同款容错：大小写 / 下划线 / 港澳繁体区域码
  assert.equal(commandZhIntent("ZH-tw"), "traditional");
  assert.equal(commandZhIntent("zh_HK"), "traditional");
  assert.equal(commandZhIntent("zh-Hant"), "traditional");
  // 未支持语言按 fallbackLng 落到简体
  assert.equal(commandZhIntent("fr"), "simplified");
  assert.equal(commandZhIntent(""), "simplified");
});

test("getCommandZhDisplay：en 返回 null（隐藏）", async () => {
  assert.equal(await getCommandZhDisplay("en"), null);
});

test("getCommandZhDisplay：zh 恒等映射", async () => {
  const display = await getCommandZhDisplay("zh");
  assert.equal(typeof display, "function");
  assert.equal(display("阿尔法"), "阿尔法");
});

test("getCommandZhDisplay：zh-TW 简转繁（真实 opencc-js 字典）", async () => {
  const display = await getCommandZhDisplay("zh-TW");
  assert.equal(typeof display, "function");
  assert.equal(display("阿尔法"), "阿爾法");
  assert.equal(display("矩阵"), "矩陣");
  assert.equal(display("贝塔"), "貝塔");
});
