import { useState, useMemo, useCallback, useDeferredValue } from "react";
import { resultDomId } from "@/utils/resultDomId";

/**
 * 段落检查结果搜索/过滤 hook。
 *
 * 提供：
 * - 文本搜索（匹配原文、修改后、解释等字段）
 * - 按修改类型过滤（change.type）
 * - 按段落编号快速跳转（scrollIntoView）
 *
 * @param {Array} results  useParagraphCheck 返回的 results 数组
 * @param {object} options
 * @param {string} options.idPrefix  滚动定位用的 DOM id 前缀，默认 "result"
 */
export default function useResultFilter(results, options = {}) {
  const { idPrefix = "result" } = options;

  const [searchQuery, setSearchQuery] = useState("");
  const [typeFilter, setTypeFilter] = useState("all");
  // 搜索过滤可能遍历大量结果，延迟值保证输入框优先响应
  const deferredSearchQuery = useDeferredValue(searchQuery);

  // 从所有 changes 中提取可用类型列表（去重、保持出现顺序）
  const availableTypes = useMemo(() => {
    const seen = new Set();
    const types = [];
    for (const r of results) {
      if (!r.changes) continue;
      for (const c of r.changes) {
        if (c.type && !seen.has(c.type)) {
          seen.add(c.type);
          types.push(c.type);
        }
      }
    }
    return types;
  }, [results]);

  const filteredResults = useMemo(() => {
    const q = deferredSearchQuery.trim().toLowerCase();
    return results.filter((r) => {
      // 类型过滤：检查该 result 的任意 change.type 是否匹配
      if (typeFilter !== "all") {
        const hasType =
          r.changes && r.changes.some((c) => c.type === typeFilter);
        if (!hasType) return false;
      }
      // 文本搜索：匹配段落编号、原文、修改后、解释
      if (q) {
        const haystacks = [];
        haystacks.push(String(r.paragraph_index));
        if (r.plain_text) haystacks.push(r.plain_text);
        if (r.original) haystacks.push(r.original);
        if (r.corrected) haystacks.push(r.corrected);
        if (r.optimized) haystacks.push(r.optimized);
        if (r.changes) {
          for (const c of r.changes) {
            if (c.original) haystacks.push(c.original);
            if (c.corrected) haystacks.push(c.corrected);
            if (c.optimized) haystacks.push(c.optimized);
            if (c.explanation) haystacks.push(c.explanation);
            if (c.reason) haystacks.push(c.reason);
            if (c.description) haystacks.push(c.description);
          }
        }
        const hit = haystacks.some((h) => h.toLowerCase().includes(q));
        if (!hit) return false;
      }
      return true;
    });
  }, [results, deferredSearchQuery, typeFilter]);

  const jumpToParagraph = useCallback(
    (paragraphIndex) => {
      // id 契约含 file_path（段落号按文件各自从 0 计），而跳转输入只有
      // 段落号：按结果列表解析该段落号可能归属的文件，逐个候选定位
      const candidates = new Set(
        results
          .filter((r) => r.paragraph_index === paragraphIndex)
          .map((r) => r.file_path || ""),
      );
      if (candidates.size === 0) candidates.add("");
      for (const fp of candidates) {
        const el = document.getElementById(
          resultDomId(idPrefix, fp, paragraphIndex),
        );
        if (el) {
          el.scrollIntoView({ behavior: "smooth", block: "center" });
          return true;
        }
      }
      return false;
    },
    [idPrefix, results],
  );

  const reset = useCallback(() => {
    setSearchQuery("");
    setTypeFilter("all");
  }, []);

  return {
    filteredResults,
    searchQuery,
    setSearchQuery,
    typeFilter,
    setTypeFilter,
    availableTypes,
    jumpToParagraph,
    reset,
    hasFilters: searchQuery.trim() !== "" || typeFilter !== "all",
  };
}
