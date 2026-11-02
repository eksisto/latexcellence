import test from "node:test";
import assert from "node:assert/strict";

import { resultDomId } from "../src/utils/resultDomId.js";

test("id 契约含 file_path 与段落号", () => {
  assert.equal(
    resultDomId("typo-result", "main.tex", 3),
    "typo-result-Pmain.tex:3",
  );
  assert.equal(
    resultDomId("term-result", "chapters/intro.tex", 0),
    "term-result-Pchapters/intro.tex:0",
  );
});

test("file_path 缺省按空串回退（与结果卡生成口径一致）", () => {
  assert.equal(resultDomId("expr-result", undefined, 7), "expr-result-P:7");
  assert.equal(resultDomId("expr-result", null, 7), "expr-result-P:7");
});

test("同段落号跨文件 id 互不相同", () => {
  assert.notEqual(
    resultDomId("typo-result", "a.tex", 2),
    resultDomId("typo-result", "b.tex", 2),
  );
});
