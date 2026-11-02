import test from "node:test";
import assert from "node:assert/strict";

import { buildParagraphMap, paragraphKey } from "../src/utils/paragraphKey.js";

test("复合键含 file_path：同段落号跨文件不碰撞", () => {
  assert.equal(paragraphKey("main.tex", 2), "main.tex:2");
  assert.notEqual(paragraphKey("a.tex", 2), paragraphKey("b.tex", 2));
  assert.equal(paragraphKey(undefined, 2), ":2");
});

test("buildParagraphMap 按复合键取回同文件段落", () => {
  const paragraphs = [
    { index: 0, file_path: "a.tex", text: "A0" },
    { index: 0, file_path: "b.tex", text: "B0" },
    { index: 1, file_path: "b.tex", text: "B1" },
  ];
  const map = buildParagraphMap(paragraphs);
  assert.equal(map.get(paragraphKey("a.tex", 0)).text, "A0");
  assert.equal(map.get(paragraphKey("b.tex", 0)).text, "B0");
  assert.equal(map.get(paragraphKey("b.tex", 1)).text, "B1");
  // 旧的纯 index 键取不到（防回归：退化为按 index 建键时同号会覆盖）
  assert.equal(map.get(0), undefined);
});
