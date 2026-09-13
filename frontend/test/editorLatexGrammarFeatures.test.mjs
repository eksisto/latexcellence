/**
 * Lezer LaTeX 子集语法阶段六b（折叠 / 环境体缩进 / 括号匹配精确化）纯函数
 * 与组装层断言（editor v3 阶段六b / 设计文档 §4.6 / 决策 Q14）。
 *
 * 被测对象：
 *   - latexGrammar/folding.js：折叠区间（环境 / 分节）、verbatim 折叠、
 *     begin↔end 配对、折叠占位符摘要（零 @codemirror 依赖的纯函数层）；
 *   - latexGrammar/indentation.js：环境体自动缩进列计算；
 *   - latexGrammar/index.js + brackets.js 组装层：openedBy/closedBy 配对
 *     prop（供 CM6 bracketMatching 树级精确配对）、语言数据
 *     （commentTokens / indentOnInput）、LanguageSupport 扩展组挂载。
 *
 * 折叠语义速记：
 *   - 环境区间 = \begin 行尾 → 匹配 \end 行首（起止同行 / 未配对 → 不可折叠）；
 *   - 分节区间 = 命令行尾 → 下一同级 / 更高级分节行首（无后继 → 文档尾；
 *     subsection 属于 section 内部，不关闭其区间）；
 *   - verbatim 类环境经 foldNodeProp 折叠（有全环境包裹节点），且不参与缩进；
 *   - 畸形输入依赖 Lezer 错误恢复：只断言可解析、查询不抛、区间合法。
 *
 * 真机项（折叠图标交互、缩进手感、占位符视觉）无法本地验证，见
 * docs/modules/editor.md。
 */

import test from "node:test";
import assert from "node:assert/strict";

import { parser } from "../src/lib/editor/latexGrammar/parser.js";
import {
  findEnvEnd,
  findEnvPairAt,
  findFoldAnchor,
  findInnermostOpenEnv,
  foldPlaceholderText,
  latexFoldRange,
  verbatimFoldRange,
} from "../src/lib/editor/latexGrammar/folding.js";
import {
  indentColumnAt,
  latexIndentColumn,
} from "../src/lib/editor/latexGrammar/indentation.js";
import {
  latexLanguage,
  latexLanguageSupport,
} from "../src/lib/editor/latexGrammar/index.js";
import { NodeProp } from "@lezer/common";
import { ensureSyntaxTree } from "@codemirror/language";
import { EditorState } from "@codemirror/state";

/** 纯字符串 → docLike（lineAt / sliceString / length，语义对齐 CM6 Text） */
function docOf(text) {
  const starts = [];
  let at = 0;
  for (const line of text.split("\n")) {
    starts.push(at);
    at += line.length + 1;
  }
  return {
    length: text.length,
    lineAt(pos) {
      const p = Math.max(0, Math.min(pos, text.length));
      let lo = 0;
      let hi = starts.length - 1;
      while (lo < hi) {
        const mid = (lo + hi + 1) >> 1;
        if (starts[mid] <= p) lo = mid;
        else hi = mid - 1;
      }
      const end = lo + 1 < starts.length ? starts[lo + 1] - 1 : text.length;
      return { from: starts[lo], to: end, text: text.slice(starts[lo], end) };
    },
    sliceString(from, to) {
      return text.slice(from, to);
    },
  };
}

/** 第 index 行（0 基）的 [from, to]（测试文档行数少，逐步步进求可读性） */
function lineRange(doc, index) {
  let pos = 0;
  for (let i = 0; i < index; i += 1) pos = doc.lineAt(pos).to + 1;
  const line = doc.lineAt(pos);
  return [line.from, line.to];
}

/** 在第 index 行内找折叠锚点（与 foldable 的逐行询问同口径） */
function anchorOnLine(tree, doc, index) {
  const [from, to] = lineRange(doc, index);
  return findFoldAnchor(tree, doc, from, to);
}

const fmt = (range) => (range ? [range.from, range.to] : null);

/* ── 环境折叠 ─────────────────────────────────────────────────────────── */

test("折叠·环境：多行环境区间为 begin 行尾到 end 行首，占位符为环境名", () => {
  const text = "\\begin{itemize}\n\\item a\n\\item b\n\\end{itemize}\n后文";
  const doc = docOf(text);
  const tree = parser.parse(text);
  const range = latexFoldRange(tree, doc, ...lineRange(doc, 0));
  assert.deepEqual(fmt(range), [15, 32]);
  assert.equal(doc.sliceString(range.from, range.to), "\n\\item a\n\\item b\n");
  assert.equal(foldPlaceholderText(tree, doc, range.from), "⟨itemize⟩");
});

test("折叠·环境：同名嵌套环境各自独立配对，不串层", () => {
  const text = [
    "\\begin{itemize}",
    "  \\begin{itemize}",
    "    deep",
    "  \\end{itemize}",
    "\\end{itemize}",
  ].join("\n");
  const doc = docOf(text);
  const tree = parser.parse(text);
  assert.deepEqual(fmt(latexFoldRange(tree, doc, ...lineRange(doc, 1))), [
    lineRange(doc, 1)[1],
    lineRange(doc, 3)[0],
  ]);
  assert.deepEqual(fmt(latexFoldRange(tree, doc, ...lineRange(doc, 0))), [
    lineRange(doc, 0)[1],
    lineRange(doc, 4)[0],
  ]);
});

test("折叠·环境：单行环境 / 未闭合 / 环境名不匹配均不可折叠", () => {
  // 单行环境：无可折叠区间
  const single = "\\begin{itemize} \\item x \\end{itemize}";
  const singleDoc = docOf(single);
  assert.equal(
    latexFoldRange(parser.parse(single), singleDoc, ...lineRange(singleDoc, 0)),
    null,
  );
  // 未闭合：无配对 \end
  const unclosed = "\\begin{itemize}\n\\item a\n无 end";
  const unclosedDoc = docOf(unclosed);
  assert.equal(
    latexFoldRange(
      parser.parse(unclosed),
      unclosedDoc,
      ...lineRange(unclosedDoc, 0),
    ),
    null,
  );
  // 名称不匹配：\end{other} 不闭合 \begin{itemize}
  const mismatch = "\\begin{itemize}\n\\item a\n\\end{other}\n尾部";
  const mismatchDoc = docOf(mismatch);
  assert.equal(
    latexFoldRange(
      parser.parse(mismatch),
      mismatchDoc,
      ...lineRange(mismatchDoc, 0),
    ),
    null,
  );
});

test("折叠·环境：注释与 \\verb 内的定界字样不参与配对", () => {
  const text = [
    "前文 % \\begin{itemize} 注释里的 begin 不算",
    "\\begin{itemize}",
    "\\verb|\\end{itemize}| 原样参数不算 end",
    "\\end{itemize}",
    "尾文",
  ].join("\n");
  const doc = docOf(text);
  const tree = parser.parse(text);
  assert.deepEqual(fmt(latexFoldRange(tree, doc, ...lineRange(doc, 1))), [
    lineRange(doc, 1)[1],
    lineRange(doc, 3)[0],
  ]);
});

test("折叠·verbatim 类环境：服务路径无环境锚点，foldNodeProp 路径可折叠", () => {
  const text = "A\n\\begin{verbatim}\nraw $x$ %c\n\\end{verbatim}\nB";
  const doc = docOf(text);
  const tree = parser.parse(text);
  // foldService 路径：\begin{verbatim} 行没有 BeginEnvironment 节点
  assert.equal(latexFoldRange(tree, doc, ...lineRange(doc, 1)), null);
  // foldNodeProp 路径：VerbatimEnvironment 包裹节点跨全环境
  let verbatimNode = null;
  tree.iterate({
    enter: (ref) => {
      if (!verbatimNode && ref.name === "VerbatimEnvironment") {
        verbatimNode = ref.node;
        return false;
      }
      return true;
    },
  });
  const range = verbatimFoldRange(doc, verbatimNode);
  assert.deepEqual(fmt(range), [lineRange(doc, 1)[1], lineRange(doc, 3)[0]]);
});

/* ── 分节折叠 ─────────────────────────────────────────────────────────── */

test("折叠·分节：subsection 不关闭 section（LaTeX 语义），同级才闭合", () => {
  const text = [
    "\\section{甲}",
    "内容一",
    "\\subsection{乙}",
    "内容二",
    "\\section{丙}",
    "内容三",
  ].join("\n");
  const doc = docOf(text);
  const tree = parser.parse(text);
  // \section{甲} 折叠到下一个同级 \section{丙} 行首
  assert.deepEqual(fmt(latexFoldRange(tree, doc, ...lineRange(doc, 0))), [
    lineRange(doc, 0)[1],
    lineRange(doc, 4)[0],
  ]);
  // \subsection{乙} 折叠到下一个更高级分节 \section{丙} 行首
  assert.deepEqual(fmt(latexFoldRange(tree, doc, ...lineRange(doc, 2))), [
    lineRange(doc, 2)[1],
    lineRange(doc, 4)[0],
  ]);
});

test("折叠·分节：星号变体同级闭合，chapter 高层级闭合 section", () => {
  const text = "\\section{甲}\n正文\n\\section*{乙}\n正文\n\\chapter{丁}\n正文";
  const doc = docOf(text);
  const tree = parser.parse(text);
  assert.deepEqual(fmt(latexFoldRange(tree, doc, ...lineRange(doc, 0))), [
    lineRange(doc, 0)[1],
    lineRange(doc, 2)[0],
  ]);
  assert.deepEqual(fmt(latexFoldRange(tree, doc, ...lineRange(doc, 4))), [
    lineRange(doc, 4)[1],
    doc.length,
  ]);
});

test("折叠·同行多锚点：取文档序首个「可折叠」锚点（findFoldAnchor 契约回归）", () => {
  // 分节在前：锚点为 SectionCommand（此前实现被行内后续锚点覆盖为末位）
  const secFirst =
    "\\section{甲}\\begin{itemize}\n条目\n\\end{itemize}\n\\section{乙}";
  const secDoc = docOf(secFirst);
  const secTree = parser.parse(secFirst);
  const secAnchor = findFoldAnchor(secTree, secDoc, ...lineRange(secDoc, 0));
  assert.equal(secAnchor.name, "SectionCommand");
  assert.deepEqual(
    fmt(latexFoldRange(secTree, secDoc, ...lineRange(secDoc, 0))),
    [lineRange(secDoc, 0)[1], lineRange(secDoc, 3)[0]],
  );
  // 环境在前：锚点为 BeginEnvironment
  const envFirst =
    "\\begin{itemize}\\section{甲}\n条目\n\\end{itemize}\n\\section{乙}";
  const envDoc = docOf(envFirst);
  const envTree = parser.parse(envFirst);
  assert.equal(
    findFoldAnchor(envTree, envDoc, ...lineRange(envDoc, 0)).name,
    "BeginEnvironment",
  );
  // 前序锚点不可折叠（单行环境）时顺延到后续分节锚点
  const mixed = "\\begin{quote} \\end{quote} \\section{乙}\n正文";
  const mixedDoc = docOf(mixed);
  const mixedTree = parser.parse(mixed);
  assert.deepEqual(
    fmt(latexFoldRange(mixedTree, mixedDoc, ...lineRange(mixedDoc, 0))),
    [lineRange(mixedDoc, 0)[1], mixedDoc.length],
  );
});

test("折叠·占位符与折叠锚点一致：同行分节+环境按实际折叠锚点取摘要", () => {
  // 折叠锚点是 SectionCommand → 摘要取分节标题（而非行内环境名）
  const secFirst =
    "\\section{甲}\\begin{itemize}\n条目\n\\end{itemize}\n\\section{乙}";
  const secDoc = docOf(secFirst);
  const secTree = parser.parse(secFirst);
  const secRange = latexFoldRange(secTree, secDoc, ...lineRange(secDoc, 0));
  assert.equal(foldPlaceholderText(secTree, secDoc, secRange.from), "⟨甲⟩");
  // 折叠锚点是 BeginEnvironment → 摘要取环境名
  const envFirst =
    "\\begin{itemize}\\section{甲}\n条目\n\\end{itemize}\n\\section{乙}";
  const envDoc = docOf(envFirst);
  const envTree = parser.parse(envFirst);
  const envRange = latexFoldRange(envTree, envDoc, ...lineRange(envDoc, 0));
  assert.equal(
    foldPlaceholderText(envTree, envDoc, envRange.from),
    "⟨itemize⟩",
  );
  // verbatim 起始行（foldNodeProp 折叠路径，无环境/分节锚点）→ ⟨verbatim⟩
  const verbatim = "A\n\\begin{verbatim}\nraw\n\\end{verbatim}\nB";
  const verbatimDoc = docOf(verbatim);
  const verbatimTree = parser.parse(verbatim);
  assert.equal(
    foldPlaceholderText(
      verbatimTree,
      verbatimDoc,
      lineRange(verbatimDoc, 1)[1],
    ),
    "⟨verbatim⟩",
  );
});

test("折叠·环境名花括号内侧空白归一：\\begin{ itemize } 与 \\end{itemize} 正常配对", () => {
  const text = "\\begin{ itemize }\n条目\n\\end{itemize}\n尾";
  const doc = docOf(text);
  const tree = parser.parse(text);
  assert.deepEqual(fmt(latexFoldRange(tree, doc, ...lineRange(doc, 0))), [
    lineRange(doc, 0)[1],
    lineRange(doc, 2)[0],
  ]);
  // 空白名仍视为无名环境，不可折叠
  const blank = "\\begin{ }\n条目\n\\end{}\n尾";
  const blankDoc = docOf(blank);
  assert.equal(
    latexFoldRange(parser.parse(blank), blankDoc, ...lineRange(blankDoc, 0)),
    null,
  );
});

test("折叠·分节：占位符取强制参数标题并超长截断；无参数回落省略号", () => {
  const longTitle = "这是一个非常非常长的分节标题用来测试截断逻辑";
  const text = `\\section{${longTitle}}\n正文`;
  const doc = docOf(text);
  const tree = parser.parse(text);
  const anchor = anchorOnLine(tree, doc, 0);
  assert.equal(anchor.name, "SectionCommand");
  // 16 字符 + 省略号
  assert.equal(
    foldPlaceholderText(tree, doc, anchor.to),
    `⟨${longTitle.slice(0, 16)}…⟩`,
  );
  // 无 Group 参数（截断形态）→ 省略号
  const bare = "\\section\n正文";
  const bareDoc = docOf(bare);
  const bareTree = parser.parse(bare);
  const bareAnchor = anchorOnLine(bareTree, bareDoc, 0);
  assert.equal(foldPlaceholderText(bareTree, bareDoc, bareAnchor.to), "⋯");
});

/* ── 畸形输入收敛 ─────────────────────────────────────────────────────── */

test("折叠·畸形输入：不崩溃且区间收敛（错误恢复树上的合法区间或 null）", () => {
  const cases = [
    "$$$ { \\begin % \\end",
    "\\begin{itemize} 缺少 \\end",
    "\\begin{} 空环境名",
    "\\begin",
    "\\subsection{",
    "\\begin{a}\n\\end{b}\n\\end{a}",
    "{ \\begin{itemize} 组内未闭合 }",
  ];
  for (const text of cases) {
    const doc = docOf(text);
    const tree = parser.parse(text);
    assert.equal(tree.length, doc.length, JSON.stringify(text));
    for (let pos = 0; pos <= doc.length; pos += 3) {
      const line = doc.lineAt(pos);
      const range = latexFoldRange(tree, doc, line.from, line.to);
      if (range) {
        assert.ok(range.from < range.to, JSON.stringify(text));
        assert.ok(range.to <= doc.length, JSON.stringify(text));
      }
      const pair = findEnvPairAt(tree, doc, pos);
      if (pair) {
        assert.ok(pair.begin.from < pair.end.from, JSON.stringify(text));
      }
      findInnermostOpenEnv(tree, doc, pos);
    }
  }
});

/* ── 环境配对（括号匹配精确化） ───────────────────────────────────────── */

test("配对：光标在 begin / end 定界节点上返回配对两端，节点外为 null", () => {
  const text = "\\begin{itemize}\n\\item a\n\\end{itemize}\n后文";
  const doc = docOf(text);
  const tree = parser.parse(text);
  const beginRange = [0, 15];
  const endRange = [24, 37];
  for (const pos of [0, 7, 15, 24, 30, 37]) {
    const pair = findEnvPairAt(tree, doc, pos);
    assert.ok(pair, `pos=${pos}`);
    assert.deepEqual([pair.begin.from, pair.begin.to], beginRange);
    assert.deepEqual([pair.end.from, pair.end.to], endRange);
  }
  // 环境体内 / 后文（不在定界节点上）不高亮
  assert.equal(findEnvPairAt(tree, doc, 20), null);
  assert.equal(findEnvPairAt(tree, doc, 38), null);
});

test("配对：同名嵌套环境按最近未闭合配对", () => {
  const text = [
    "\\begin{itemize}",
    "  \\begin{itemize}",
    "    x",
    "  \\end{itemize}",
    "\\end{itemize}",
  ].join("\n");
  const doc = docOf(text);
  const tree = parser.parse(text);
  // 光标在内层 begin 标记上（缩进后第 3 字符起）→ 配对内层 end
  const inner = findEnvPairAt(tree, doc, lineRange(doc, 1)[0] + 2);
  assert.deepEqual(
    [inner.begin.from, inner.end.from],
    [lineRange(doc, 1)[0] + 2, lineRange(doc, 3)[0] + 2],
  );
  // 光标在外层 begin 标记上 → 配对外层 end
  const outer = findEnvPairAt(tree, doc, lineRange(doc, 0)[0]);
  assert.deepEqual(
    [outer.begin.from, outer.end.from],
    [lineRange(doc, 0)[0], lineRange(doc, 4)[0]],
  );
});

test("配对：孤儿 end（环境名不匹配）无高亮", () => {
  const text = "\\begin{itemize}\n\\item a\n\\end{other}\n\\end{itemize}";
  const doc = docOf(text);
  const tree = parser.parse(text);
  // 光标在 \end{other} 上：名字对不上最内层开环境 → 无配对
  assert.equal(findEnvPairAt(tree, doc, lineRange(doc, 2)[0]), null);
  // 光标在 \end{itemize} 上：与最内层开环境 begin{itemize} 配对
  const pair = findEnvPairAt(tree, doc, lineRange(doc, 3)[0]);
  assert.ok(pair);
  assert.deepEqual([pair.begin.from, pair.begin.to], lineRange(doc, 0));
});

test("配对：findEnvEnd 纯函数供折叠与括号匹配复用", () => {
  const text = "\\begin{a}\n\\begin{b}\nx\n\\end{b}\n\\end{a}";
  const doc = docOf(text);
  const tree = parser.parse(text);
  const outerBegin = anchorOnLine(tree, doc, 0);
  const end = findEnvEnd(tree, doc, outerBegin);
  assert.deepEqual([end.from, end.to], lineRange(doc, 4));
});

/* ── 环境体自动缩进 ───────────────────────────────────────────────────── */

test("缩进：环境体内换行缩进为 begin 列 + 单位，嵌套自然叠加", () => {
  const text = [
    "\\begin{itemize}",
    "  \\begin{enumerate}",
    "    deep",
    "  \\end{enumerate}",
    "\\end{itemize}",
  ].join("\n");
  const doc = docOf(text);
  const tree = parser.parse(text);
  const UNIT = 2;
  const TAB = 8;
  // \begin{itemize} 后新行 → 0 + 2
  assert.equal(
    latexIndentColumn(tree, doc, lineRange(doc, 1)[0], UNIT, TAB),
    2,
  );
  // 内层环境体内（deep 行）→ 内层 begin 列 2 + 2
  assert.equal(
    latexIndentColumn(tree, doc, lineRange(doc, 2)[0], UNIT, TAB),
    4,
  );
  // 内层 \end 行 → 与内层 begin 列对齐（详见对齐用例）
  assert.equal(
    latexIndentColumn(tree, doc, lineRange(doc, 3)[0], UNIT, TAB),
    2,
  );
});

test("缩进：\\end 行与所属 begin 起始列对齐", () => {
  const text = [
    "\\begin{itemize}",
    "  \\begin{enumerate}",
    "    x",
    "  \\end{enumerate}",
    "\\end{itemize}",
  ].join("\n");
  const doc = docOf(text);
  const tree = parser.parse(text);
  assert.equal(latexIndentColumn(tree, doc, lineRange(doc, 3)[0], 2, 8), 2);
  assert.equal(latexIndentColumn(tree, doc, lineRange(doc, 4)[0], 2, 8), 0);
});

test("缩进：无开环境返回 null；begin 行带缩进时继承其列", () => {
  // 顶层无环境：pos 取第二行行首
  const plain = "正文一行\n第二行";
  const plainDoc = docOf(plain);
  assert.equal(
    latexIndentColumn(
      parser.parse(plain),
      plainDoc,
      lineRange(plainDoc, 1)[0],
      2,
      8,
    ),
    null,
  );
  // 带缩进的 begin：体内容 = begin 列 + 单位
  const text = "  \\begin{itemize}\n\\item a";
  const doc = docOf(text);
  assert.equal(
    latexIndentColumn(parser.parse(text), doc, lineRange(doc, 1)[0], 2, 8),
    4,
  );
});

test("缩进：verbatim 类环境体内不参与缩进（保持现状）", () => {
  const text = [
    "\\begin{itemize}",
    "  \\begin{verbatim}",
    "   raw   text",
    "  \\end{verbatim}",
    "\\end{itemize}",
  ].join("\n");
  const doc = docOf(text);
  const tree = parser.parse(text);
  assert.equal(latexIndentColumn(tree, doc, lineRange(doc, 2)[0], 2, 8), null);
  assert.equal(latexIndentColumn(tree, doc, lineRange(doc, 3)[0], 2, 8), null);
  // verbatim 之外恢复正常（\end{itemize} 对齐 begin 列）
  assert.equal(latexIndentColumn(tree, doc, lineRange(doc, 4)[0], 2, 8), 0);
});

test("缩进：indentColumnAt 的 tab 展开与 CM countColumn 同义", () => {
  assert.equal(indentColumnAt("  \\begin", 2, 8), 2);
  assert.equal(indentColumnAt("\t\\begin", 1, 8), 8);
  assert.equal(indentColumnAt("\t\tx", 2, 4), 8);
  assert.equal(indentColumnAt("abc", 3, 8), 3);
});

test("缩进：findInnermostOpenEnv 纯函数（嵌套回溯 / 孤儿 end 收敛）", () => {
  const text = "\\begin{a}\n\\begin{b}\nx";
  const doc = docOf(text);
  const tree = parser.parse(text);
  // pos 在 x 行 → 最内层开环境为 b
  const inner = findInnermostOpenEnv(tree, doc, lineRange(doc, 2)[0]);
  assert.equal(inner.name, "BeginEnvironment");
  assert.deepEqual([inner.from, inner.to], lineRange(doc, 1));
  // pos 在 b 的 begin 行首 → b 尚未越过 pos，最内层为 a
  const outer = findInnermostOpenEnv(tree, doc, lineRange(doc, 1)[0]);
  assert.deepEqual([outer.from, outer.to], lineRange(doc, 0));
  // 孤儿 end 不产生幻觉开环境
  const orphan = "\\end{a}\nx";
  const orphanDoc = docOf(orphan);
  assert.equal(
    findInnermostOpenEnv(
      parser.parse(orphan),
      orphanDoc,
      lineRange(orphanDoc, 1)[0],
    ),
    null,
  );
});

/* ── 组装层：配对 prop 与语言数据 ─────────────────────────────────────── */

/** 组装层（含六b props / 扩展组）的 EditorState 辅助 */
function stateOf(text) {
  return EditorState.create({
    doc: text,
    extensions: [latexLanguageSupport()],
  });
}

test("组装层：openedBy/closedBy prop 挂到分组与数学定界节点（CM bracketMatching 树级精确配对）", () => {
  // 注：顶层裸 ( ) 不建树节点（与语法既定口径一致），数学界内圆括号有节点
  const text = "{a} $x$ $$y$$ \\[z\\] \\(w\\) [b] $p(q)r$";
  const state = stateOf(text);
  const tree = ensureSyntaxTree(state, text.length);
  const nodeAt = (name) => {
    let found = null;
    tree.iterate({
      enter: (ref) => {
        if (!found && ref.name === name) {
          found = ref.node;
          return false;
        }
        return true;
      },
    });
    return found;
  };
  assert.deepEqual(nodeAt("OpenBrace").type.prop(NodeProp.closedBy), [
    "CloseBrace",
  ]);
  assert.deepEqual(nodeAt("CloseBrace").type.prop(NodeProp.openedBy), [
    "OpenBrace",
  ]);
  assert.deepEqual(nodeAt("MathDollar").type.prop(NodeProp.closedBy), [
    "MathDollar",
  ]);
  assert.deepEqual(nodeAt("MathDollarDollar").type.prop(NodeProp.closedBy), [
    "MathDollarDollar",
  ]);
  assert.deepEqual(nodeAt("DBracketOpen").type.prop(NodeProp.closedBy), [
    "DBracketClose",
  ]);
  assert.deepEqual(nodeAt("DParenOpen").type.prop(NodeProp.closedBy), [
    "DParenClose",
  ]);
  assert.deepEqual(nodeAt("OpenBracket").type.prop(NodeProp.closedBy), [
    "CloseBracket",
  ]);
  assert.deepEqual(nodeAt("OpenParen").type.prop(NodeProp.closedBy), [
    "CloseParen",
  ]);
});

test("组装层：语言数据保留 commentTokens 并新增 indentOnInput 触发正则", () => {
  const state = stateOf("\\end{itemize}");
  assert.deepEqual(state.languageDataAt("commentTokens", 0), [{ line: "%" }]);
  const rules = state.languageDataAt("indentOnInput", 13);
  assert.equal(rules.length, 1);
  assert.ok(rules[0].test("\\end{itemize}"));
  assert.ok(rules[0].test("  \\end{a}"));
  assert.ok(!rules[0].test("\\endx{a}"));
  assert.ok(!rules[0].test("\\begin{a}"));
});

test("组装层：latexLanguage 为 LRLanguage 且 support 扩展组已挂载", () => {
  const support = latexLanguageSupport();
  assert.equal(support.language, latexLanguage);
  // foldService / indentService / latexEnvPairHighlight 三件套
  assert.ok(support.support.length >= 1);
});

/* ── 大文档回归（2MB 全量解析 + 六b 查询耗时数据点） ───────────────────── */

test("大文档回归：2MB 解析 + 折叠/缩进/配对抽样查询（耗时数据点，见 docs/modules/editor.md）", () => {
  const paragraph = [
    "\\section{第 %d 节}",
    "正文段落，包含 \\textbf{粗体} 与行内公式 $a^{2}+b_{1}=c$，以及",
    "\\begin{itemize}\\item 条目一 \\item 条目二\\end{itemize}。",
    "\\begin{verbatim}raw %content $x$\\end{verbatim}",
    "% 行注释 \\section{不解析}",
    "",
  ].join("\n");
  const target = 2 * 1024 * 1024;
  const text = paragraph.repeat(Math.ceil(target / paragraph.length));
  const doc = docOf(text);
  const parseStart = performance.now();
  const tree = parser.parse(text);
  const parseMs = Math.round(performance.now() - parseStart);
  assert.equal(tree.length, doc.length);
  // 折叠 / 缩进 / 配对抽样查询（文档 20 处均匀取样）
  const queryStart = performance.now();
  let folds = 0;
  for (let i = 0; i < 20; i += 1) {
    const pos = Math.floor((doc.length / 20) * i);
    const line = doc.lineAt(pos);
    if (latexFoldRange(tree, doc, line.from, line.to)) folds += 1;
    latexIndentColumn(tree, doc, pos, 2, 8);
    findEnvPairAt(tree, doc, pos);
  }
  const queryMs = Math.round(performance.now() - queryStart);
  console.log(
    `[latexGrammar 六b 大文档回归] ${(doc.length / 1024 / 1024).toFixed(2)} MB 全量解析 ${parseMs} ms，20 处折叠/缩进/配对抽样查询合计 ${queryMs} ms（命中折叠 ${folds} 处）`,
  );
  assert.ok(parseMs < 10000, `2MB 解析耗时异常: ${parseMs} ms`);
  // 抽样查询整体应在数十至百毫秒量级（无 O(n²) 劣化）。上限是退化哨兵：
  // 慢速 CI 上正常实现也不过数秒，阈值取 8s 只拦截算法退化。
  assert.ok(queryMs < 8000, `六b 查询耗时异常: ${queryMs} ms`);
});
