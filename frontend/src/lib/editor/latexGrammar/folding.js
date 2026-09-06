/**
 * LaTeX 子集语法的折叠 / 环境配对纯函数层（editor v3 阶段六b / 决策 Q14）。
 *
 * 本模块零外部依赖（不 import @lezer/* / @codemirror/*），以鸭子类型消费：
 *   - tree：@lezer/common 的解析树（parser.parse 产物；只用 iterate /
 *     topNode / childBefore / prevSibling / firstChild / resolveInner 与
 *     ref.name / ref.from / ref.to。注意 iterate 的 SyntaxNodeRef 在遍历
 *     中被复用，离开回调即失效，跨回调保存必须转为位置快照）；
 *   - doc：CM6 Text 或测试适配器（length / lineAt(pos) → {from, to, text} /
 *     sliceString(from, to)）。
 *
 * ── 为什么折叠走 foldService 而非 foldNodeProp（Lezer 惯例选型说明）──────
 *   1. foldNodeProp 要求「单个树节点跨越整个折叠区间」（CM 的
 *      syntaxFolding 只在包含行尾的节点的祖先栈上查 prop）。本语法没有
 *      Environment 包裹节点——`\begin{env}` 与 `\end{env}` 是兄弟节点，
 *      且「环境名相同才配对」无法用 CFG 表达（Lezer 若强行加包裹规则，
 *      `\begin{a}…\end{b}` 会被贪心错误配对，未闭合环境更会吞掉后文，
 *      比现状差得多）；
 *   2. 分节块（\section 到下一同级/更高级分节）在 LaTeX 里没有显式结束
 *      标记，同样不存在对应树节点；
 *   3. 因此环境/分节折叠经 foldService（逐行询问、返回 {from, to}）在
 *      组装层接线，配对逻辑全部落在本纯函数层（node --test 直测）；
 *      verbatim 类环境例外——grammar 中存在跨全环境的 VerbatimEnvironment
 *      包裹节点，走 CM 原生 foldNodeProp 路径（见 brackets.js / index.js）。
 *
 * 性能护栏（2MB 大文档逐行询问 foldable 的场景）：
 *   - 前向配对扫描带「命中即抛出哨兵提前退出」+ 扫描字符上限
 *     （FOLD_SCAN_LIMIT）；结果按 (tree, 锚点位置) 记忆化——折叠 gutter、
 *     括号匹配、foldCode 对同一树状态的重复询问只算一次；
 *   - 后向开环境回溯带节点访问次数上限（OPEN_ENV_WALK_LIMIT）。
 */

/** 前向配对扫描的字符上限（超过按「不可折叠」收敛；跨 1MB 的环境/分节视为病态） */
export const FOLD_SCAN_LIMIT = 1024 * 1024;

/** 后向开环境回溯的节点访问上限（防超大文档逐键缩进的尾延迟） */
export const OPEN_ENV_WALK_LIMIT = 50_000;

/** 分节命令（含星号变体）切片 → 折叠层级（越小越高） */
const SECTION_LEVELS = {
  part: 0,
  chapter: 1,
  section: 2,
  subsection: 3,
  subsubsection: 4,
  paragraph: 5,
};

/** 环境定界节点切片 → 环境名（`\begin {x}` 允许 ws 被跳过；空名视为无名） */
const ENV_SLICE_RE = /^\\(begin|end)\s*\{([^{}]*)\}$/;
/** 分节命令切片（SectionCommand 节点只覆盖命令 token 本身，含星号） */
const SECTION_SLICE_RE =
  /^\\(part|chapter|section|subsection|subsubsection|paragraph)\*?$/;
/** 折叠占位符里环境名 / 标题摘要的长度上限 */
const PLACEHOLDER_MAX = 16;

/** 前向扫描提前退出的哨兵（iterate 无 break，命中即抛出收敛） */
const SCAN_DONE = { scanDone: true };

/**
 * 从 BeginEnvironment / EndEnvironment 节点切片提取环境名。
 *
 * EnvName 外的花括号内侧空白（`\begin{ env }`）不产生独立 token，但落在
 * 定界节点跨度内、会进入切片——此处 trim 归一，保证与 `\end{env}` 正常
 * 配对（真实 LaTeX 对 `{` 后空白同样跳过）。
 *
 * @param {object} doc docLike（sliceString）
 * @param {object} node 环境定界节点（ref 即可）
 * @returns {string|null} 环境名；形态不符 / 空名返回 null
 */
export function envNameOfNode(doc, node) {
  const m = ENV_SLICE_RE.exec(doc.sliceString(node.from, node.to));
  const name = m && m[2].trim();
  return name ? name : null;
}

/**
 * 从 SectionCommand 节点切片解析分节层级。
 * @returns {number|null} 层级（part=0 … paragraph=5）；非分节命令返回 null
 */
export function sectionLevelOfNode(doc, node) {
  const m = SECTION_SLICE_RE.exec(doc.sliceString(node.from, node.to));
  return m ? SECTION_LEVELS[m[1]] : null;
}

/**
 * 在 [lineStart, lineEnd] 内找第一个「可折叠锚点」节点（环境 \begin 或
 * 分节命令，文档序）。节点必须起于该行内；verbatim 环境不在此列（其
 * 折叠走 foldNodeProp 原生路径，见 verbatimFoldRange）。
 *
 * 注意：iterate 的 SyntaxNodeRef 在遍历过程中被复用，离开回调后即失效，
 * 因此这里返回 {name, from, to} 快照而非 ref 本身；命中首个锚点后以
 * null 守卫防后续兄弟节点覆盖（iterate 无 break，只能遍历完整行）。
 *
 * @returns {{name: string, from: number, to: number}|null} 锚点快照
 */
export function findFoldAnchor(tree, doc, lineStart, lineEnd) {
  let anchor = null;
  tree.iterate({
    from: lineStart,
    to: Math.min(lineEnd, tree.length),
    enter: (ref) => {
      if (ref.from < lineStart) return true;
      if (
        !anchor &&
        (ref.name === "BeginEnvironment" || ref.name === "SectionCommand")
      ) {
        anchor = { name: ref.name, from: ref.from, to: ref.to };
        return false; // 锚点子树内无其他定界节点，不再下探
      }
      return true;
    },
  });
  return anchor;
}

/** 内部：带记忆化与前向扫描的「begin → 匹配 end」查找（end 位置快照或 null） */
function findEnvEndUncached(tree, doc, beginNode, anchorName) {
  const stack = [];
  let found = null;
  const scanFrom = beginNode.to;
  try {
    tree.iterate({
      from: scanFrom,
      to: Math.min(doc.length, beginNode.from + FOLD_SCAN_LIMIT),
      enter: (ref) => {
        // 锚点自身及其子树恰好结束于扫描起点，iterate 会访问到——跳过，
        // 防止锚点把自己压栈（否则同名环境被自己的 begin 抵消，永不配对）
        if (ref.to <= scanFrom) return false;
        if (ref.name === "BeginEnvironment") {
          const name = envNameOfNode(doc, ref);
          if (name) stack.push(name);
          return false; // begin 标记子树内不可能有定界节点
        }
        if (ref.name === "EndEnvironment") {
          const name = envNameOfNode(doc, ref);
          if (name) {
            const idx = stack.lastIndexOf(name);
            if (idx >= 0) {
              stack.length = idx; // 闭合一层嵌套开环境（含其内未闭合者）
            } else if (stack.length === 0 && name === anchorName) {
              found = { from: ref.from, to: ref.to }; // 栈空且同名 → 即锚点环境的结束标记
              throw SCAN_DONE;
            }
          }
          return false;
        }
        if (
          ref.name === "LineComment" ||
          ref.name === "VerbatimEnvironment" ||
          ref.name === "VerbCommand"
        ) {
          return false; // 注释 / verbatim / \verb 内不可能有定界节点
        }
        return true;
      },
    });
  } catch (error) {
    if (error !== SCAN_DONE) throw error;
  }
  return found;
}

/** 前向查找结果的 per-tree 记忆化（同一树状态内重复询问零成本） */
const scanCache = new WeakMap();
const SCAN_CACHE_LIMIT = 512;

function cachedScan(tree, key, compute) {
  let cache = scanCache.get(tree);
  if (!cache) {
    cache = new Map();
    scanCache.set(tree, cache);
  }
  if (cache.has(key)) return cache.get(key);
  if (cache.size >= SCAN_CACHE_LIMIT) cache.clear();
  const value = compute();
  cache.set(key, value);
  return value;
}

/**
 * \begin{env} 节点 → 匹配的 \end{env} 节点（前向栈匹配，文档序；
 * 跨 Group / 数学界等作用域仍按文档序配对——LaTeX 环境本就允许跨花括号组）。
 * @returns {object|null}
 */
export function findEnvEnd(tree, doc, beginNode) {
  const anchorName = envNameOfNode(doc, beginNode);
  if (!anchorName) return null;
  return cachedScan(tree, `env:${beginNode.from}`, () =>
    findEnvEndUncached(tree, doc, beginNode, anchorName),
  );
}

/**
 * 环境折叠区间：`\begin` 所在行行尾 → 匹配 `\end` 所在行行首
 * （起止单行 / 未配对 / 无名环境返回 null）。折叠后占位符出现在
 * \begin 行尾，\end 行保持可见。
 * @returns {{from: number, to: number}|null}
 */
export function envFoldRange(tree, doc, beginNode) {
  const end = findEnvEnd(tree, doc, beginNode);
  if (!end) return null;
  const from = doc.lineAt(beginNode.from).to;
  const to = doc.lineAt(end.from).from;
  return to > from ? { from, to } : null;
}

/**
 * 分节折叠区间：分节命令所在行行尾 → 下一同级 / 更高级分节命令所在行
 * 行首（无后继时到文档尾）。LaTeX 分节语义：\subsection 属于 \section
 * 内部，不关闭其折叠区间。
 * @returns {{from: number, to: number}|null}
 */
export function sectionFoldRange(tree, doc, sectionNode) {
  const level = sectionLevelOfNode(doc, sectionNode);
  if (level === null) return null;
  const next = cachedScan(tree, `sec:${sectionNode.from}:${level}`, () => {
    let found = null;
    const scanFrom = sectionNode.to;
    try {
      tree.iterate({
        from: scanFrom,
        to: Math.min(doc.length, sectionNode.from + FOLD_SCAN_LIMIT),
        enter: (ref) => {
          if (ref.to <= scanFrom) return false; // 跳过锚点自身及其子树
          if (ref.name === "SectionCommand") {
            const other = sectionLevelOfNode(doc, ref);
            if (other !== null && other <= level) {
              found = { from: ref.from, to: ref.to };
              throw SCAN_DONE;
            }
            return false; // 分节命令节点内无子分节
          }
          if (
            ref.name === "LineComment" ||
            ref.name === "VerbatimEnvironment" ||
            ref.name === "VerbCommand"
          ) {
            return false;
          }
          return true;
        },
      });
    } catch (error) {
      if (error !== SCAN_DONE) throw error;
    }
    return found;
  });
  const from = doc.lineAt(sectionNode.from).to;
  const to = next ? doc.lineAt(next.from).from : doc.length;
  return to > from ? { from, to } : null;
}

/**
 * verbatim 类环境折叠区间（foldNodeProp 路径：VerbatimEnvironment 包裹
 * 节点跨全环境）。区间口径与环境折叠一致：起始行行尾 → \end 标记行行首。
 * @param {object} doc docLike
 * @param {object} node VerbatimEnvironment 节点 ref
 * @returns {{from: number, to: number}|null}
 */
export function verbatimFoldRange(doc, node) {
  // 结构：VerbatimEnvironment { VerbatimEnv|… { BeginV* VerbatimBody EndV* } }
  const variant = node.lastChild;
  const endMarker = variant && variant.lastChild;
  if (!endMarker || !/^End/.test(endMarker.name)) return null;
  const from = doc.lineAt(node.from).to;
  const to = doc.lineAt(endMarker.from).from;
  return to > from ? { from, to } : null;
}

/**
 * 行内首个「可折叠」锚点（文档序）：环境 / 分节锚点逐个尝试区间计算，
 * 不可折叠（单行环境 / 未配对 / 无名环境）则继续行内后续锚点，首个命中
 * 经哨兵提前退出。latexFoldRange 与 foldPlaceholderText 共用，保证占位符
 * 与实际折叠区间描述同一锚点。
 * @returns {{name: string, from: number, to: number}|null}
 */
function firstFoldableAnchor(tree, doc, lineStart, lineEnd) {
  let anchor = null;
  try {
    tree.iterate({
      from: lineStart,
      to: Math.min(lineEnd, tree.length),
      enter: (ref) => {
        if (ref.from < lineStart) return true;
        if (ref.name === "BeginEnvironment" || ref.name === "SectionCommand") {
          const snapshot = { name: ref.name, from: ref.from, to: ref.to };
          const range =
            snapshot.name === "BeginEnvironment"
              ? envFoldRange(tree, doc, snapshot)
              : sectionFoldRange(tree, doc, snapshot);
          if (range) {
            anchor = snapshot;
            throw SCAN_DONE;
          }
          return false; // 该锚点不可折叠，继续行内后续锚点
        }
        return true;
      },
    });
  } catch (error) {
    if (error !== SCAN_DONE) throw error;
  }
  return anchor;
}

/**
 * foldService 适配入口：询问 [lineStart, lineEnd] 行是否可折叠。
 *
 * 按文档序返回行内首个「可折叠」锚点的区间（见 firstFoldableAnchor）。
 * @returns {{from: number, to: number}|null}
 */
export function latexFoldRange(tree, doc, lineStart, lineEnd) {
  const anchor = firstFoldableAnchor(tree, doc, lineStart, lineEnd);
  if (!anchor) return null;
  // 可折叠性已由 firstFoldableAnchor 判定；复算走 cachedScan 记忆化，零成本
  return anchor.name === "BeginEnvironment"
    ? envFoldRange(tree, doc, anchor)
    : sectionFoldRange(tree, doc, anchor);
}

/**
 * pos 处最内层「未闭合」环境（向后回溯，作用域感知）。
 *
 * 算法：自树顶沿 childBefore / prevSibling 链做反向文档序递归下降，维护
 * 「待闭合环境名」栈——遇完整 \end 入栈，遇 \begin 时若栈顶同名则出栈
 * （已被其后 \end 闭合），否则该 \begin 即为 pos 处最内层开环境（反向序
 * 下第一个未闭合 begin 必然最内层）。访问节点数超 OPEN_ENV_WALK_LIMIT
 * 按无开环境收敛（超大文档回溯视为病态，保持现状缩进）。
 *
 * 注意：
 *   - `\begin` / `\end` 是兄弟节点而非包裹节点，祖先链上不存在环境节点，
 *     必须按兄弟序列回溯（resolveInner 的祖先链在此不可用——pos 落在节点
 *     之间时它会返回外层容器节点）；
 *   - verbatim 环境体内没有 Begin/End 节点（原样吞掉），天然不参与回溯。
 *
 * @returns {object|null} 开环境的 BeginEnvironment 节点（稳定 SyntaxNode）
 */
export function findInnermostOpenEnv(tree, doc, pos) {
  const top = tree.topNode ?? null;
  if (!top) return null;
  const pending = [];
  let visited = 0;
  let found = null;
  const walk = (node) => {
    for (let child = node.childBefore(pos); child; child = child.prevSibling) {
      if (found || visited > OPEN_ENV_WALK_LIMIT) return;
      visited += 1;
      if (
        child.name === "BeginEnvironment" ||
        child.name === "EndEnvironment"
      ) {
        const name = envNameOfNode(doc, child);
        if (name) {
          if (child.name === "EndEnvironment") {
            if (child.to <= pos) pending.push(name); // 未打完的 \end 尚不闭合
          } else if (child.from <= pos) {
            if (pending.length === 0 || !pending.includes(name)) {
              found = child; // 无人闭合 → pos 处最内层开环境
              return;
            }
            // 栈顶同名（正常闭合）或深层同名（畸形嵌套）→ 收敛栈
            pending.length = pending.lastIndexOf(name);
          }
        }
        continue; // 定界标记子树内无其他定界节点
      }
      if (child.firstChild) walk(child);
    }
  };
  walk(top);
  return found;
}

/**
 * begin/end 环境配对（括号匹配高亮适配）：光标 pos 邻接（含两端边缘）的
 * BeginEnvironment / EndEnvironment 节点，返回配对两端区间；无配对返回
 * null（不高亮——语法树精确语义下不做文本回退猜测）。
 * @returns {{begin: object, end: object}|null}
 */
export function findEnvPairAt(tree, doc, pos) {
  for (const side of [-1, 1]) {
    let node = tree.resolveInner(Math.min(pos, tree.length), side);
    for (; node; node = node.parent) {
      if (pos < node.from || pos > node.to) continue;
      if (node.name === "BeginEnvironment") {
        const end = findEnvEnd(tree, doc, node);
        if (end) return { begin: node, end };
        return null; // 光标在 begin 上且无配对 → 不高亮
      }
      if (node.name === "EndEnvironment") {
        const begin = findInnermostOpenEnv(tree, doc, node.from);
        if (begin && envNameOfNode(doc, begin) === envNameOfNode(doc, node)) {
          return { begin, end: node };
        }
        return null;
      }
    }
  }
  return null;
}

/** 摘要文本截断（超长加省略号） */
function truncate(text, max = PLACEHOLDER_MAX) {
  const flat = text.replace(/\s+/g, " ").trim();
  return flat.length > max ? `${flat.slice(0, max)}…` : flat;
}

/**
 * 折叠占位符摘要（codeFolding preparePlaceholder 适配）：与 latexFoldRange
 * 同锚点口径——行内首个可折叠锚点是环境则给 `⟨环境名⟩`、是分节则给
 * `⟨标题摘要⟩`（同线首个 Group 强制参数）；verbatim 类环境行无环境 /
 * 分节锚点（折叠走 foldNodeProp 路径），取 `⟨verbatim⟩`；无锚点回落 `⋯`。
 *
 * @param {object} tree 解析树
 * @param {object} doc docLike
 * @param {number} from 折叠区间起点（锚点所在行的行尾）
 * @returns {string}
 */
export function foldPlaceholderText(tree, doc, from) {
  const line = doc.lineAt(from);
  const anchor = firstFoldableAnchor(tree, doc, line.from, line.to);
  if (anchor && anchor.name === "BeginEnvironment") {
    const envName = envNameOfNode(doc, anchor);
    return envName ? `⟨${truncate(envName, PLACEHOLDER_MAX)}⟩` : "⋯";
  }
  if (anchor && anchor.name === "SectionCommand") {
    // 分节命令后（同一行内）的第一个 Group 即强制参数 {标题}
    //（iterate 的 ref 是复用对象，nextSibling 导航不可靠，按文档序收集）
    let title = null;
    tree.iterate({
      from: anchor.to,
      to: Math.min(line.to, tree.length),
      enter: (ref) => {
        if (ref.name === "Group") {
          title = doc.sliceString(ref.from + 1, ref.to - 1);
          return false;
        }
        return true;
      },
    });
    return title && title.trim()
      ? `⟨${truncate(title, PLACEHOLDER_MAX)}⟩`
      : "⋯";
  }
  if (!anchor) {
    // verbatim 类环境的起始行：折叠由 foldNodeProp（VerbatimEnvironment
    // 包裹节点）产生，该行无环境 / 分节锚点
    let verbatim = false;
    tree.iterate({
      from: line.from,
      to: Math.min(line.to, tree.length),
      enter: (ref) => {
        if (ref.from < line.from) return true;
        if (ref.name === "VerbatimEnvironment") {
          verbatim = true;
          return false;
        }
        return true;
      },
    });
    if (verbatim) return "⟨verbatim⟩";
  }
  return "⋯";
}
