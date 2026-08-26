import test from "node:test";
import assert from "node:assert/strict";

import { pinyin } from "pinyin-pro";

import {
  DEFAULT_MATCH_LIMIT,
  buildCommandIndex,
  fillPinyinSequences,
  getCommandMatcherIndex,
  listCommands,
  matchScore,
  normalizeQuery,
  searchCommands,
  SCORE,
} from "../src/lib/editor/matcher.js";
import { extractEnvironments } from "../src/lib/editor/latexCompletion.js";
import { LATEX_COMMANDS } from "../src/data/latexCommands/index.js";

/** 共享索引：一次性预计算（模拟加载期行为，勿在用例内反复构建） */
const index = fillPinyinSequences(buildCommandIndex(), pinyin);

test("normalizeQuery：去空白、大小写归一", () => {
  assert.equal(normalizeQuery("  Alpha "), "alpha");
  assert.equal(normalizeQuery("a\tb\nc"), "abc");
  assert.equal(normalizeQuery("阿尔法"), "阿尔法");
  assert.equal(normalizeQuery(null), "");
});

test("中文子串路：query 含于 zh 即命中", () => {
  const hits = searchCommands(index, "阿尔法");
  assert.ok(hits.length > 0);
  assert.equal(hits[0].name, "alpha");
  // 部分中文名同样命中
  const partial = searchCommands(index, "矩阵");
  assert.ok(partial.some((e) => e.name === "matrix"));
  const cases = searchCommands(index, "分段函数");
  assert.equal(cases[0].name, "cases");
});

test("全拼路：aerfa / juzhen 命中", () => {
  assert.equal(searchCommands(index, "aerfa")[0].name, "alpha");
  const juzhen = searchCommands(index, "juzhen");
  assert.equal(juzhen[0].name, "matrix");
});

test("首字母路：ae 命中阿尔法；jz 覆盖矩阵家族（名词首字母子串）", () => {
  assert.equal(searchCommands(index, "ae")[0].name, "alpha");
  const jz = searchCommands(index, "jz");
  // 脚注（jiao zhu）首字母精确命中应排在前面
  assert.equal(jz[0].name, "footnote");
  // 无括号矩阵等修饰语在前的条目经首字母子串路仍可被找到
  assert.ok(jz.some((e) => e.name === "matrix"));
});

test("英文前缀/子串路：al → alpha 置顶；大小写不敏感", () => {
  assert.equal(searchCommands(index, "al")[0].name, "alpha");
  assert.equal(searchCommands(index, "ALPHA")[0].name, "alpha");
  const sub = searchCommands(index, "trix"); // 子串命中 matrix 家族
  assert.ok(sub.some((e) => e.name === "matrix"));
});

test("四路得分归一排序：同刻度下精确/前缀优先，同分按库序", () => {
  // 查询 al：alpha 与 align 家族同为英文前缀 92 分，
  // alpha 因数据库原始顺序靠前（希腊字母类目在前）而置顶
  const hits = searchCommands(index, "al").map((e) => e.name);
  assert.equal(hits[0], "alpha");
  assert.ok(hits.indexOf("alpha") < hits.indexOf("align"));
  assert.ok(hits.indexOf("align") < hits.indexOf("aligned"));
});

test("matchScore 刻度：精确路径得分高于对应前缀与子串", () => {
  const row = index.find((r) => r.entry.name === "alpha");
  assert.equal(matchScore(row, "alpha"), SCORE.ENGLISH_EXACT);
  assert.equal(matchScore(row, "al"), SCORE.ENGLISH_PREFIX);
  assert.equal(matchScore(row, "lph"), SCORE.ENGLISH_SUBSTRING);
  assert.equal(matchScore(row, "阿尔法"), SCORE.ZH_EXACT);
  assert.equal(matchScore(row, "阿"), SCORE.ZH_PREFIX);
  assert.equal(matchScore(row, "尔法"), SCORE.ZH_SUBSTRING);
  assert.equal(matchScore(row, "aerfa"), SCORE.PINYIN_EXACT);
  assert.equal(matchScore(row, "aerf"), SCORE.PINYIN_PREFIX);
  assert.equal(matchScore(row, "erfa"), SCORE.PINYIN_SUBSTRING);
  // 阿尔法的首字母序列为 aef；查询 ae 命中的是全拼前缀（88）而非首字母
  assert.equal(matchScore(row, "aef"), SCORE.INITIALS_EXACT);
  assert.equal(matchScore(row, "ae"), SCORE.PINYIN_PREFIX);
  // 单字符不放行首字母子串（f 在全拼 aerfa 中出现，走的是全拼子串路）
  assert.equal(matchScore(row, "f"), SCORE.PINYIN_SUBSTRING);
  assert.equal(matchScore(row, "q"), 0);
  assert.equal(matchScore(row, "zzzz"), 0);
  assert.equal(matchScore(row, ""), 0);
});

test("归一排序稳定性：同分条目按数据库原始顺序复现", () => {
  // 构造两条完全同名等分的合成条目
  const twins = [
    {
      name: "tw",
      insert: "\\tw",
      zh: "孪生",
      category: "operator",
      pkg: null,
      grid: null,
    },
    {
      name: "tw",
      insert: "\\tw",
      zh: "孪生",
      category: "operator",
      pkg: null,
      grid: null,
    },
  ];
  const idx = fillPinyinSequences(buildCommandIndex(twins), pinyin);
  const runA = searchCommands(idx, "tw").map((e) => e.insert);
  const runB = searchCommands(idx, "tw").map((e) => e.insert);
  assert.deepEqual(runA, ["\\tw", "\\tw"]);
  assert.deepEqual(runA, runB);

  // 反向插入顺序时 tie-break 跟随 order 而非随机
  const reversed = fillPinyinSequences(
    buildCommandIndex([...twins].reverse()),
    pinyin,
  );
  assert.deepEqual(
    searchCommands(reversed, "tw").map((e) => e.insert),
    ["\\tw", "\\tw"],
  );
});

test("空查询与未命中返回空数组；limit 生效", () => {
  assert.deepEqual(searchCommands(index, ""), []);
  assert.deepEqual(searchCommands(index, "不存在词条xyzq"), []);
  const limited = searchCommands(index, "a", { limit: 3 });
  assert.equal(limited.length, 3);
});

test("混合 CJK+ASCII 查询只走中文子串路（拼音序列不含 CJK）", () => {
  assert.deepEqual(searchCommands(index, "阿尔法x"), []);
});

test("listCommands：默认列表按原始顺序且不打分", () => {
  const defaults = listCommands(index);
  assert.equal(defaults[0], LATEX_COMMANDS[0]);
  assert.ok(defaults.length <= 60);
});

test("listCommands：{limit:N} 精确生效且按索引构造序截取", () => {
  // 前提：主库条目多于默认上限，limit 失效时会回退到 DEFAULT_MATCH_LIMIT
  assert.ok(LATEX_COMMANDS.length > DEFAULT_MATCH_LIMIT);
  // 回归（latexCompletion 环境补全）：曾以裸数字调用
  // listCommands(idx, 30)，对象解构形参下 limit 静默失效回退默认 60；
  // 修复为 { limit: 30 } 后必须精确截取前 30 条（索引构造序）
  const limited = listCommands(index, { limit: 30 });
  assert.equal(limited.length, 30);
  assert.deepEqual(limited, LATEX_COMMANDS.slice(0, 30));

  // 环境名索引同型构建（buildCommandIndex 保序）：>30 条时同样只出 30 条
  const synthetic = buildCommandIndex(
    Array.from({ length: 40 }, (_, i) => ({
      name: `synthenv${i}`,
      insert: `\\begin{synthenv${i}}`,
      zh: `合成环境${i}`,
      category: "environment",
      pkg: null,
      grid: null,
    })),
  );
  assert.equal(listCommands(synthetic, { limit: 30 }).length, 30);

  // 实际环境索引规模小于 30：上限不截断，全量返回且保持提取顺序
  const envRows = buildCommandIndex(
    extractEnvironments().map(({ name, entry }) => ({ ...entry, name })),
  );
  const envList = listCommands(envRows, { limit: 30 });
  assert.deepEqual(
    envList,
    extractEnvironments().map(({ entry }) => entry),
  );
});

test("getCommandMatcherIndex：动态加载并填充拼音缓存（共享实例）", async () => {
  const shared = await getCommandMatcherIndex();
  const again = await getCommandMatcherIndex();
  assert.equal(shared, again, "应复用同一缓存索引");
  const row = shared.find((r) => r.entry.name === "alpha");
  assert.equal(row.fullPinyin, "aerfa");
  assert.equal(row.initials, "aef");
});
