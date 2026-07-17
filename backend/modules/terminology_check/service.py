"""
术语一致性检查业务逻辑。

流程（三步走）：
    1. 扫描目标文件的段落，通过 latex_parser 剥离为纯文本
    2. 调用 LLM 从每段提取关键术语（概念、缩写、专有名词）
    3. 程序去重（仅当字符完全匹配时视为重复）
    4. 调用 LLM 对去重后的术语列表进行语义聚类
       - 识别语义相同的独立术语组
       - 从每组中选择一个最标准术语作为推荐项
       - 以 JSON 格式返回映射关系
    5. 程序解析聚类结果，为每个段落生成修正后的文本
       - 将属于同一组的非推荐变体替换为推荐项
       - 供前端逐段审核应用，用户可自定义替换内容

支持 \\input 解析：
    当 resolve_input=True 时，递归展开主文件中的 \\input/\\include 引用，
    将所有子文件纳入处理范围。术语提取跨所有文件全局执行（保证一致性），
    结果按文件分组返回。
"""

import asyncio
import re
from pathlib import Path
from typing import Any, Optional

from backend.core.logger import get_logger
from backend.core.project_manager import get_project_manager
from backend.core.llm_client import get_llm_client
from backend.core.config import get_config
from backend.utils.latex_parser import LatexParser
from backend.modules.terminology_check.extractor import TermExtractor, TermItem
from backend.modules.terminology_check.models import TermChange, TermParagraphResult
from backend.utils.input_resolver import resolve_inputs


_MARKER_PATTERN = re.compile(r"__[A-Z]+_\d{4}__")


def _deduplicate_terms(all_terms: list[dict[str, Any]]) -> list[str]:
    """
    对 LLM 提取的所有术语进行去重（仅当字符完全匹配时视为重复）。

    返回：
        去重后的术语字符串列表（保持首次出现顺序）
    """
    seen: set[str] = set()
    unique: list[str] = []
    for t in all_terms:
        term_str = t["term"]
        if term_str not in seen:
            seen.add(term_str)
            unique.append(term_str)
    return unique


class TerminologyCheckService:
    """
    术语一致性检查服务。

    用法：
        svc = TerminologyCheckService()
        preview = svc.preview_paragraphs("main.tex")
        results = await svc.check_terminology("main.tex")
        svc.apply_changes("main.tex", [{"paragraph_index": 0, "new_text": "..."}])

    支持 \\input 解析：
        preview = svc.preview_paragraphs("main.tex", resolve_input=True)
        results = await svc.check_terminology_stream("main.tex", resolve_input=True)
    """

    def __init__(self):
        self._logger = get_logger("terminology_check")
        self._parser = LatexParser()
        self._extractor = TermExtractor(logger_name="terminology_check")
        self._marker_maps: dict[str, dict[int, dict[str, str]]] = {}
        self._file_path: Optional[str] = None

    def _get_files(
        self, file_path: str, resolve_input: bool
    ) -> list[str]:
        """解析 \\input 依赖，返回待处理文件列表。"""
        if not resolve_input:
            return [file_path]
        pm = get_project_manager()
        root = pm._project_info.root_path
        files = resolve_inputs(file_path, root)
        self._logger.info(
            f"Resolved \\input tree from {file_path}: {len(files)} file(s)"
        )
        return files

    def _process_paragraphs(
        self, paragraphs: list, file_path: str
    ) -> list[dict[str, Any]]:
        """对单个文件的段落执行 marker 替换，返回结果列表。"""
        if file_path not in self._marker_maps:
            self._marker_maps[file_path] = {}
        result = []
        for para in paragraphs:
            stripped, marker_map = self._parser.strip_to_plain_text(para.text)
            self._marker_maps[file_path][para.index] = marker_map
            result.append({
                "index": para.index,
                "text": para.text,
                "plain_text": stripped,
                "line_start": para.line_start,
                "line_end": para.line_end,
                "file_path": file_path,
            })
        return result

    def preview_paragraphs(
        self, file_path: str, resolve_input: bool = False
    ) -> dict[str, Any]:
        """
        预览指定文件的段落。

        参数：
            file_path: 文件路径
            resolve_input: 是否递归展开 \\input 引用

        返回：
            { paragraphs: [...], file_path: "...", total: N }
        """
        pm = get_project_manager()
        files = self._get_files(file_path, resolve_input)

        self._file_path = file_path
        self._marker_maps = {}

        all_paragraphs: list[dict[str, Any]] = []
        for f in files:
            paragraphs = pm.get_paragraphs(f)
            all_paragraphs.extend(self._process_paragraphs(paragraphs, f))

        self._logger.info(
            f"Previewed {len(all_paragraphs)} paragraphs from "
            f"{len(files)} file(s) starting at {file_path}"
        )
        return {
            "paragraphs": all_paragraphs,
            "file_path": file_path,
            "total": len(all_paragraphs),
        }

    async def check_terminology(
        self,
        file_path: str,
        paragraph_indices: Optional[list[int]] = None,
        resolve_input: bool = False,
    ) -> dict[str, Any]:
        """
        对项目进行术语一致性检查。

        参数：
            file_path: 主目标文件路径（预览和结果显示用）
            paragraph_indices: 要检查的段落索引列表，None 表示全部
            resolve_input: 是否递归展开 \\input 引用

        返回：
            { results: [...], file_path: "...", total_issues: N }
        """
        pm = get_project_manager()
        cfg = get_config()
        files = self._get_files(file_path, resolve_input)

        self._file_path = file_path
        self._marker_maps = {}

        all_para_data: list[dict[str, Any]] = []
        for f in files:
            paragraphs = pm.get_paragraphs(f)
            for para in paragraphs:
                stripped, marker_map = self._parser.strip_to_plain_text(para.text)
                if f not in self._marker_maps:
                    self._marker_maps[f] = {}
                self._marker_maps[f][para.index] = marker_map
                all_para_data.append({
                    "file_path": f,
                    "index": para.index,
                    "text": para.text,
                    "plain_text": stripped,
                    "marker_map": marker_map,
                })

        has_api_key = bool(cfg.llm.api_key)

        if not has_api_key:
            self._logger.warning("No API key — using fallback term extraction")
            return self._check_no_llm(file_path, all_para_data)

        llm = get_llm_client()
        sem = asyncio.Semaphore(cfg.llm.max_concurrency)

        async def _limited(coro):
            async with sem:
                return await coro

        all_terms: list[dict[str, Any]] = []
        batch_size = cfg.features.typo_check.batch_size

        for batch_start in range(0, len(all_para_data), batch_size):
            batch = all_para_data[batch_start: batch_start + batch_size]
            tasks = []
            for data in batch:
                plain = data["plain_text"]
                if not plain.strip():
                    tasks.append(self._empty_terms(data["index"]))
                else:
                    tasks.append(
                        self._extractor.extract_terms(
                            data["index"], plain,
                            batch_label=f"B{batch_start // batch_size}/",
                        )
                    )
            batch_terms = await asyncio.gather(
                *(_limited(t) for t in tasks)
            )
            for i, data in enumerate(batch):
                for t in batch_terms[i]:
                    all_terms.append({
                        "term": t.term,
                        "term_type": t.term_type,
                        "paragraph_index": data["index"],
                        "file_path": data["file_path"],
                        "context": t.context,
                    })

            if batch_start + batch_size < len(all_para_data):
                await asyncio.sleep(cfg.llm.request_interval)

        # 程序去重：仅当字符完全匹配时视为重复
        unique_terms = _deduplicate_terms(all_terms)
        self._logger.info(
            f"Deduplicated {len(all_terms)} terms to {len(unique_terms)} unique"
        )

        # LLM 语义聚类：识别语义相同的术语组并选定推荐项
        clusters = await self._extractor.cluster_terms(unique_terms)

        # 程序解析聚类结果，为每个段落生成修正文本
        results = self._build_results(all_para_data, all_terms, clusters)

        total_issues = sum(1 for r in results for c in r["changes"])
        return {
            "results": results,
            "file_path": file_path,
            "total_issues": total_issues,
        }

    async def check_terminology_stream(
        self, file_path: str, resolve_input: bool = False
    ):
        """
        术语检查流式版本（SSE）。

        逐段 yield 事件：progress / result / done。
        """
        pm = get_project_manager()
        cfg = get_config()
        files = self._get_files(file_path, resolve_input)

        self._file_path = file_path
        self._marker_maps = {}

        all_para_data: list[dict[str, Any]] = []
        for f in files:
            paragraphs = pm.get_paragraphs(f)
            for para in paragraphs:
                stripped, marker_map = self._parser.strip_to_plain_text(para.text)
                if f not in self._marker_maps:
                    self._marker_maps[f] = {}
                self._marker_maps[f][para.index] = marker_map
                all_para_data.append({
                    "file_path": f,
                    "index": para.index,
                    "text": para.text,
                    "plain_text": stripped,
                    "marker_map": marker_map,
                })

        total = len(all_para_data)
        yield {"type": "progress", "current": 0, "total": total}

        has_api_key = bool(cfg.llm.api_key)

        if not has_api_key:
            yield {
                "type": "error",
                "message": "No API key configured — terminology clustering requires LLM",
                "retrying": False,
            }
            results = self._build_results_no_llm(all_para_data)
            for r in results:
                yield {"type": "result", "paragraph_index": r["paragraph_index"], "data": r}
                yield {"type": "progress", "current": r["paragraph_index"] + 1, "total": total}
            yield {"type": "done"}
            return

        all_terms: list[dict[str, Any]] = []

        for i, data in enumerate(all_para_data):
            plain = data["plain_text"]
            f = data["file_path"]

            if not plain.strip():
                yield {
                    "type": "progress",
                    "current": i + 1,
                    "total": total,
                    "file_path": f,
                }
                continue

            try:
                terms = await self._extractor.extract_terms(
                    data["index"], plain, batch_label="",
                )
                for t in terms:
                    all_terms.append({
                        "term": t.term,
                        "term_type": t.term_type,
                        "paragraph_index": data["index"],
                        "file_path": data["file_path"],
                        "context": t.context,
                    })
                yield {
                    "type": "progress",
                    "current": i + 1,
                    "total": total,
                    "file_path": f,
                }
            except Exception as e:
                self._logger.error(
                    f"Error extracting terms from {f} P{data['index']}: {e}")
                yield {
                    "type": "error",
                    "message": f"{f} P{data['index']}: {e}",
                    "retrying": False,
                }
                yield {
                    "type": "progress",
                    "current": i + 1,
                    "total": total,
                    "file_path": f,
                }

            await asyncio.sleep(cfg.llm.request_interval)

        # 程序去重：仅当字符完全匹配时视为重复
        unique_terms = _deduplicate_terms(all_terms)
        self._logger.info(
            f"Deduplicated {len(all_terms)} terms to {len(unique_terms)} unique"
        )

        # LLM 语义聚类
        clusters = await self._extractor.cluster_terms(unique_terms)

        # 程序解析聚类结果，为每个段落生成修正文本
        results = self._build_results(all_para_data, all_terms, clusters)

        for r in results:
            yield {"type": "result", "paragraph_index": r["paragraph_index"], "data": r}

        yield {"type": "done"}

    def apply_changes(
        self, file_path: str, changes: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """
        将用户确认的修改写回源文件。

        参数：
            file_path: 默认文件路径（当 change 中无 file_path 时使用）
            changes: [{ "paragraph_index": 0, "new_text": "...", "file_path": "..." }]

        返回：
            { applied: N, total: M }
        """
        pm = get_project_manager()
        applied = 0

        for change in changes:
            idx = change["paragraph_index"]
            new_plain_text = change["new_text"]
            target_file = change.get("file_path", file_path)

            file_maps = self._marker_maps.get(target_file, {})
            marker_map = file_maps.get(idx, {})

            restored = self._parser.restore_from_markers(
                new_plain_text, marker_map)

            pm.apply_change(target_file, idx, restored)
            applied += 1
            self._logger.info(
                f"Applied terminology change to paragraph {idx} in {target_file}")

        return {"applied": applied, "total": len(changes)}

    def _build_results(
        self,
        all_para_data: list[dict[str, Any]],
        all_terms: list[dict[str, Any]],
        clusters: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """
        基于 LLM 聚类结果，为每个段落生成修正结果。

        对每个包含非推荐变体的段落，生成 corrected 文本
        （将变体替换为推荐项）。

        参数：
            all_para_data: 所有段落数据（含 file_path）
            all_terms: 原始提取的术语列表（含 paragraph_index, file_path）
            clusters: LLM 聚类结果 [{"recommended": str, "variants": [str]}]
        """
        # 构建 variant → recommended 映射
        replace_map: dict[str, str] = {}
        for cluster in clusters:
            recommended = cluster["recommended"]
            for variant in cluster["variants"]:
                if variant != recommended:
                    replace_map[variant] = recommended

        # 按 (file_path, 段落索引) 收集术语
        terms_by_para: dict[tuple[str, int], list[str]] = {}
        for t in all_terms:
            key = (t.get("file_path", ""), t["paragraph_index"])
            if key not in terms_by_para:
                terms_by_para[key] = []
            terms_by_para[key].append(t["term"])

        results: list[dict[str, Any]] = []
        for data in all_para_data:
            idx = data["index"]
            f = data.get("file_path", "")
            original = data["plain_text"]
            corrected = original
            changes: list[dict[str, Any]] = []

            para_terms = terms_by_para.get((f, idx), [])
            # 对每个需要替换的变体生成一条 change
            seen_variants: set[str] = set()
            for term_str in para_terms:
                if term_str in replace_map and term_str not in seen_variants:
                    seen_variants.add(term_str)
                    recommended = replace_map[term_str]
                    change = {
                        "type": "inconsistency",
                        "concept": recommended,
                        "original": term_str,
                        "corrected": recommended,
                        "explanation": (
                            f"术语 \"{term_str}\" 建议统一为 "
                            f"\"{recommended}\""
                        ),
                    }
                    changes.append(change)

            # 按变体长度降序替换，避免短变体匹配长变体子串
            sorted_changes = sorted(
                changes, key=lambda c: len(c["original"]), reverse=True
            )
            for change in sorted_changes:
                if change["original"] in corrected:
                    corrected = corrected.replace(
                        change["original"], change["corrected"]
                    )

            mark_original = original
            mark_corrected = corrected if changes else mark_original

            # 收集此段落的所有术语（去重）
            terms_found = list(dict.fromkeys(para_terms))

            result = {
                "paragraph_index": idx,
                "original": mark_original,
                "corrected": mark_corrected,
                "terms": terms_found,
                "changes": changes,
                "text": data["text"],
                "plain_text": data["plain_text"],
                "file_path": f,
            }
            results.append(result)

        return results

    def _build_results_no_llm(
        self,
        all_para_data: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """
        无 LLM 时生成基础结果。
        无法进行语义聚类，仅返回段落术语标签（来自规则降级提取），不生成 changes。
        """
        extractor = TermExtractor()
        results: list[dict[str, Any]] = []
        for data in all_para_data:
            terms = extractor.extract_terms_fallback(data["plain_text"])
            terms_found = list(dict.fromkeys(t.term for t in terms))
            results.append({
                "paragraph_index": data["index"],
                "original": data["plain_text"],
                "corrected": data["plain_text"],
                "terms": terms_found,
                "changes": [],
                "text": data["text"],
                "plain_text": data["plain_text"],
                "file_path": data.get("file_path", ""),
            })
        return results

    def _check_no_llm(
        self,
        file_path: str,
        all_para_data: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """无 API Key 时降级为纯规则检查。"""
        results = self._build_results_no_llm(all_para_data)
        total_issues = sum(1 for r in results for c in r.get("changes", []))
        return {
            "results": results,
            "file_path": file_path,
            "total_issues": total_issues,
        }

    async def _empty_terms(self, paragraph_index: int) -> list:
        return []

    def _empty_result(self, data: dict[str, Any]) -> dict[str, Any]:
        return {
            "paragraph_index": data["index"],
            "original": data["plain_text"],
            "corrected": data["plain_text"],
            "terms": [],
            "changes": [],
            "text": data["text"],
            "plain_text": data["plain_text"],
        }
