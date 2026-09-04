/**
 * 行级 diff 纯模块（editor-productivity-plan 阶段八：修改行色条，
 * 借鉴 Notepad++ 8.4.6 Change History margin）。
 *
 * computeChangedLines(before, after) 返回 after 中「与 before 存在差异」的
 * 行号集合（1 基）。算法：
 *   1. 全文相同直通（键入回显 / 撤回到保存点的高频路径，O(1)）；
 *   2. 剪去公共前后缀（正常编辑的中段只剩几行，Myers 主循环开销可忽略）；
 *   3. 中段跑 Myers O(ND) 最短编辑距离回溯，收集 b 侧插入/替换行；
 *      纯删除块不标（after 中已无对应行，与 Notepad++ 行为一致）；
 *   4. 防退化护栏：中段总行数超上限或 Myers 步数预算耗支（整文档重排/
 *      格式化等全量替换场景）时，中段整段标记为变更——结果正确，只是
 *      不再追求最小区块，避免 O(ND) 退化为灾难级耗时。
 *
 * 纯函数，供 node --test 直接单测。
 */

/** 中段总行数上限：超过直接整段标记（跳过 Myers） */
const DIFF_MIDDLE_LINE_CAP = 6000;

/** Myers 内层步数预算：超支回退整段标记 */
const DIFF_MAX_WORK = 200000;

/**
 * @param {string[]} a 中段旧行
 * @param {string[]} b 中段新行
 * @returns {Set<number>|null} b 侧变更行（0 基局部索引）；null = 预算超支
 */
function myersChangedBIndices(a, b) {
  const n = a.length;
  const m = b.length;
  if (n === 0) {
    // 纯插入：b 全部是新增行
    const all = new Set();
    for (let i = 0; i < m; i++) all.add(i);
    return all;
  }
  if (m === 0) return new Set(); // 纯删除：after 无对应行
  if (n + m > DIFF_MIDDLE_LINE_CAP) return null;

  // 行文本 intern 成整数，主循环只比整数
  const intern = new Map();
  const idOf = (line) => {
    let id = intern.get(line);
    if (id === undefined) {
      id = intern.size;
      intern.set(line, id);
    }
    return id;
  };
  const ai = new Int32Array(n);
  const bi = new Int32Array(m);
  for (let i = 0; i < n; i++) ai[i] = idOf(a[i]);
  for (let j = 0; j < m; j++) bi[j] = idOf(b[j]);

  const maxD = n + m;
  const offset = maxD;
  let v = new Int32Array(2 * maxD + 1);
  const trace = [];
  let foundD = -1;
  let work = 0;

  for (let d = 0; d <= maxD; d++) {
    trace.push(v.slice());
    for (let k = -d; k <= d; k += 2) {
      let x;
      if (k === -d || (k !== d && v[offset + k - 1] < v[offset + k + 1])) {
        x = v[offset + k + 1]; // 下移（b 侧插入）
      } else {
        x = v[offset + k - 1] + 1; // 右移（a 侧删除）
      }
      let y = x - k;
      while (x < n && y < m && ai[x] === bi[y]) {
        x++;
        y++;
        if (++work > DIFF_MAX_WORK) return null;
      }
      v[offset + k] = x;
      if (x >= n && y >= m) {
        foundD = d;
        break;
      }
    }
    if (foundD >= 0) break;
  }
  if (foundD < 0) return null;

  // 回溯：down 移动（插入）标记被插入的 b 行（回溯点 y 前一行）
  const changed = new Set();
  let x = n;
  let y = m;
  for (let d = foundD; d >= 1; d--) {
    const vPrev = trace[d]; // trace[d] = 深度 d 处理前（即 d-1 后）的 v
    const k = x - y;
    const down =
      k === -d || (k !== d && vPrev[offset + k - 1] < vPrev[offset + k + 1]);
    const prevK = down ? k + 1 : k - 1;
    const prevX = vPrev[offset + prevK];
    const prevY = prevX - prevK;
    if (down) changed.add(prevY);
    x = prevX;
    y = prevY;
  }
  return changed;
}

/**
 * 计算 after 相对 before 的变更行集合。
 * @param {string} before 基线全文
 * @param {string} after 当前全文
 * @returns {Set<number>} after 中变更行的行号（1 基）；纯删除块不产生行号
 */
export function computeChangedLines(before, after) {
  if (before === after) return new Set();
  const a = before.split("\n");
  const b = after.split("\n");
  const minLen = Math.min(a.length, b.length);
  let start = 0;
  while (start < minLen && a[start] === b[start]) start++;
  let endA = a.length;
  let endB = b.length;
  while (endA > start && endB > start && a[endA - 1] === b[endB - 1]) {
    endA--;
    endB--;
  }
  const changed = new Set();
  const midB = myersChangedBIndices(a.slice(start, endA), b.slice(start, endB));
  if (midB === null) {
    // 预算超支：中段整段标记（结果正确优先于最小区块）
    for (let i = start; i < endB; i++) changed.add(i + 1);
    return changed;
  }
  for (const local of midB) changed.add(start + local + 1);
  return changed;
}
