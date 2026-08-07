/**
 * code_review 结果页锚点/采纳的纯逻辑工具。
 *
 * 坐标系约定：
 * - 锚点：建议的 original 片段在 formattedText 中的起始 offset；
 * - 采纳：编辑文本中 original 片段的实际 [start, end) 区间。
 * 两处共用同一套定位逻辑：精确匹配优先，折叠空白宽松匹配兜底
 * （latexindent 可能调整行内空白/缩进，LLM 引用的片段未必逐字命中）。
 */

/** 折叠连续空白（含换行/缩进）为单个空格，用于宽松匹配。 */
export function collapseWhitespace(s) {
  return s.replace(/\s+/g, " ");
}

/** 返回 needle 在 text 中的所有起始下标。 */
export function findAllOffsets(text, needle) {
  const offsets = [];
  let from = 0;
  let idx;
  while ((idx = text.indexOf(needle, from)) !== -1) {
    offsets.push(idx);
    from = idx + 1;
  }
  return offsets;
}

/**
 * 返回 text 中 needle 所有匹配里距 target 下标最近的一个（平手取更靠前者），
 * 无匹配返回 -1。用于把"采纳建议"定位到锚点指向的那一处，而非总是首次出现处。
 */
export function nearestOccurrence(text, needle, target) {
  let best = -1;
  let bestDist = Infinity;
  let from = 0;
  let idx;
  while ((idx = text.indexOf(needle, from)) !== -1) {
    const dist = Math.abs(idx - target);
    if (dist < bestDist) {
      bestDist = dist;
      best = idx;
    }
    from = idx + 1;
  }
  return best;
}

/**
 * 把折叠文本中的下标 ci 映射回真实文本的下标。
 * 折叠文本中空白 run 计 1 个字符；映射结果指向该 run 的起始或结束边界。
 */
export function mapCollapsedIndex(text, ci) {
  let pos = 0;
  let i = 0;
  while (i < text.length && pos < ci) {
    if (/\s/.test(text[i])) {
      pos += 1;
      while (i < text.length && /\s/.test(text[i])) i += 1;
    } else {
      pos += 1;
      i += 1;
    }
  }
  return i;
}

/**
 * 在 text 中定位 needle，返回真实文本中的 [start, end) 区间。
 * 1) 优先精确匹配：取距 target 最近的一处；
 * 2) 失败时折叠空白宽松匹配，同样取距 target 最近的一处。
 * 找不到返回 null。
 */
export function locateSpan(text, needle, target = 0) {
  if (!needle) return null;

  const exact = nearestOccurrence(text, needle, target);
  if (exact >= 0) return { start: exact, end: exact + needle.length };

  const collapsedNeedle = collapseWhitespace(needle);
  if (!collapsedNeedle) return null;
  const collapsedText = collapseWhitespace(text);
  let best = null;
  let bestDist = Infinity;
  for (const ci of findAllOffsets(collapsedText, collapsedNeedle)) {
    const start = mapCollapsedIndex(text, ci);
    const end = mapCollapsedIndex(text, ci + collapsedNeedle.length);
    const dist = Math.abs(start - target);
    if (dist < bestDist) {
      bestDist = dist;
      best = { start, end };
    }
  }
  return best;
}

/** 定位 needle 的起始下标（锚点用）；找不到返回 -1。 */
export function locateSnippet(text, needle) {
  return locateSpan(text, needle)?.start ?? -1;
}

/**
 * 计算每条建议在 formattedText 中的锚点 offset，按 offset 升序返回。
 *
 * 设计要点（修复锚点错位）：
 * - 重复引用同一片段的建议按出现顺序"消费"第 1、2、… 个匹配，避免多条
 *   批注全部堆在首次出现处；
 * - 精确匹配失败时用折叠空白的宽松匹配兜底，防止 latexindent 重排空白后
 *   锚点整体丢失；
 * - 输出按 offset 升序，AnnotatedSource 依赖有序输入渲染（原实现未排序，
 *   建议顺序与源码顺序不一致时会把文本切片切错、锚点放错位置）。
 */
export function buildAnchors(formattedText, suggestions) {
  const consumed = new Map();
  return suggestions
    .map((s, i) => {
      if (!s.original) return null;
      const offsets = findAllOffsets(formattedText, s.original);
      const offset =
        offsets.length > 0
          ? offsets[Math.min(consumed.get(s.original) ?? 0, offsets.length - 1)]
          : locateSnippet(formattedText, s.original);
      if (offset < 0) return null;
      consumed.set(s.original, (consumed.get(s.original) ?? 0) + 1);
      return { index: i + 1, offset, description: s.description };
    })
    .filter(Boolean)
    .sort((a, b) => a.offset - b.offset);
}

/**
 * 在 formattedText 坐标下，applyEdits 中起始位置 <= off 的编辑对 off 累计造成的位移。
 * 采纳建议按锚点 offset（formattedText 坐标）定位；先前采纳的替换会改动其左侧文本，
 * 需用累计位移把锚点位置换算到当前已编辑文本中。
 */
export function cumulativeShift(applyEdits, off) {
  let shift = 0;
  for (const e of applyEdits) {
    if (e.offset <= off) shift += e.addedLen - e.removedLen;
  }
  return shift;
}
