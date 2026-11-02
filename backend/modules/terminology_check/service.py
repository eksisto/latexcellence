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
from typing import Any, ClassVar

from backend.core.base_paragraph_check import BaseParagraphCheckService
from backend.core.config import get_config
from backend.core.model_router import has_api_key_for
from backend.modules.terminology_check.extractor import TermExtractor
from backend.utils.latex_parser import MARKER_PATTERN


def _bounded_replace(text: str, variant: str, recommended: str) -> tuple[str, int]:
    """带词边界的变体替换，返回（替换后文本, 替换次数）。

    前后向断言排除字母 / 数字 / 下划线邻接：防止短变体命中其它单词的
    子串（如变体 "ML" 命中 "HTML"），也排除 marker 字符 ``_`` 邻接，
    防止变体（如 "CMD"）命中 ``__CMD_0000__`` 内部后损坏 marker。
    """
    pattern = re.compile(
        r"(?<![A-Za-z0-9_])" + re.escape(variant) + r"(?![A-Za-z0-9_])"
    )
    return pattern.subn(recommended, text)


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


class TerminologyCheckService(BaseParagraphCheckService):
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

    # LLM 槽位路由（multi-model §6）：逐段高频调用，适合快速模型
    llm_role: ClassVar[str] = "terminology_check"

    def __init__(self):
        super().__init__("terminology_check")
        self._extractor = TermExtractor(logger_name="terminology_check")

    # ── 核心检查逻辑（术语提取 → 聚类 → 生成结果）─────────────────────────

    async def check_terminology(
        self,
        file_path: str,
        paragraph_indices: list[int] | None = None,
        resolve_input: bool = False,
        workspace_id: str = "",
    ) -> dict[str, Any]:
        """
        对项目进行术语一致性检查。

        参数：
            file_path: 主目标文件路径（预览和结果显示用）
            paragraph_indices: 要检查的段落索引列表，None 表示全部
            resolve_input: 是否递归展开 \\input 引用
            workspace_id: 工作区唯一标识符

        返回：
            { results: [...], file_path: "...", total_issues: N }
        """
        cfg = get_config()
        files = self._get_files(file_path, resolve_input, workspace_id)

        self._file_path = file_path
        self._marker_maps = {}

        all_para_data = self._collect_para_data(files, workspace_id)
        # 请求可指定只检查部分段落：过滤后驱动提取与结果构建。
        # marker 映射与上下文仍按全部段落收集，apply 路径不受过滤影响。
        para_data = self._filter_targets(all_para_data, file_path, paragraph_indices)

        operation_id = self._generate_operation_id()
        file_md5s = self._capture_file_md5s(files, workspace_id)
        self._store_context(operation_id, self._marker_maps, file_path, file_md5s)

        has_api_key = has_api_key_for(self.llm_role)

        if not has_api_key:
            self._logger.warning("No API key — using fallback term extraction")
            return self._check_no_llm(file_path, para_data, operation_id)

        sem = asyncio.Semaphore(cfg.llm.max_concurrency)

        async def _limited(coro):
            async with sem:
                return await coro

        all_terms: list[dict[str, Any]] = []
        batch_size = cfg.features.terminology_check.batch_size

        for batch_start in range(0, len(para_data), batch_size):
            batch = para_data[batch_start : batch_start + batch_size]
            tasks = []
            for data in batch:
                plain = data["plain_text"]
                if not plain.strip():
                    tasks.append(self._empty_terms(data["para_index"]))
                else:
                    tasks.append(
                        self._extractor.extract_terms(
                            data["para_index"],
                            plain,
                            batch_label=f"B{batch_start // batch_size}/",
                        )
                    )
            batch_terms = await asyncio.gather(*(_limited(t) for t in tasks))
            for i, data in enumerate(batch):
                for t in batch_terms[i]:
                    all_terms.append(
                        {
                            "term": t.term,
                            "term_type": t.term_type,
                            "paragraph_index": data["para_index"],
                            "file_path": data["file_path"],
                            "context": t.context,
                        }
                    )

            if batch_start + batch_size < len(all_para_data):
                await asyncio.sleep(cfg.llm.request_interval)

        # 程序去重：仅当字符完全匹配时视为重复
        unique_terms = _deduplicate_terms(all_terms)
        self._logger.info(
            f"Deduplicated {len(all_terms)} terms to " f"{len(unique_terms)} unique"
        )

        # LLM 语义聚类：识别语义相同的术语组并选定推荐项
        clusters = await self._extractor.cluster_terms(unique_terms)

        # 程序解析聚类结果，为每个段落生成修正文本
        results = self._build_results(para_data, all_terms, clusters)

        total_issues = sum(1 for r in results for c in r["changes"])
        self._logger.info(
            f"Terminology check completed for {file_path} " f"[op={operation_id}]"
        )
        return {
            "results": results,
            "file_path": file_path,
            "total_issues": total_issues,
            "operation_id": operation_id,
        }

    async def check_terminology_stream(
        self, file_path: str, resolve_input: bool = False, workspace_id: str = ""
    ):
        """
        术语检查流式版本（SSE）。

        逐段 yield 事件：progress / result / done。
        done 事件包含 operation_id，前端 apply 时需回传。
        """
        cfg = get_config()
        files = self._get_files(file_path, resolve_input, workspace_id)

        self._file_path = file_path
        self._marker_maps = {}

        all_para_data = self._collect_para_data(files, workspace_id)

        operation_id = self._generate_operation_id()
        file_md5s = self._capture_file_md5s(files, workspace_id)
        self._store_context(operation_id, self._marker_maps, file_path, file_md5s)

        total = len(all_para_data)
        yield {"type": "progress", "current": 0, "total": total}

        has_api_key = has_api_key_for(self.llm_role)

        if not has_api_key:
            yield {
                "type": "error",
                "message": (
                    "No API key configured — " "terminology clustering requires LLM"
                ),
                "retrying": False,
            }
            results = self._build_results_no_llm(all_para_data)
            for r in results:
                yield {
                    "type": "result",
                    "paragraph_index": r["paragraph_index"],
                    "data": r,
                }
                yield {
                    "type": "progress",
                    "current": r["paragraph_index"] + 1,
                    "total": total,
                }
            yield {"type": "done", "operation_id": operation_id}
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
                    data["para_index"],
                    plain,
                    batch_label="",
                )
                for t in terms:
                    all_terms.append(
                        {
                            "term": t.term,
                            "term_type": t.term_type,
                            "paragraph_index": data["para_index"],
                            "file_path": data["file_path"],
                            "context": t.context,
                        }
                    )
                yield {
                    "type": "progress",
                    "current": i + 1,
                    "total": total,
                    "file_path": f,
                }
            except Exception as e:
                self._logger.error(
                    f"Error extracting terms from " f"{f} P{data['para_index']}: {e}"
                )
                yield {
                    "type": "error",
                    "message": f"{f} P{data['para_index']}: {e}",
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
            f"Deduplicated {len(all_terms)} terms to " f"{len(unique_terms)} unique"
        )

        # LLM 语义聚类
        clusters = await self._extractor.cluster_terms(unique_terms)

        # 程序解析聚类结果，为每个段落生成修正文本
        results = self._build_results(all_para_data, all_terms, clusters)

        for r in results:
            yield {
                "type": "result",
                "paragraph_index": r["paragraph_index"],
                "data": r,
            }

        self._logger.info(
            f"Terminology stream check completed for {file_path} "
            f"[op={operation_id}]"
        )
        yield {"type": "done", "operation_id": operation_id}

    # ── 结果构建 ────────────────────────────────────────────────────────────

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
            idx = data["para_index"]
            f = data.get("file_path", "")
            original = data["text"]
            corrected = data["plain_text"]
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
                            f'Term "{term_str}" is recommended to be unified as '
                            f'"{recommended}"'
                        ),
                    }
                    changes.append(change)

            # 按变体长度降序替换，避免短变体匹配长变体子串；带词边界匹配
            sorted_changes = sorted(
                changes, key=lambda c: len(c["original"]), reverse=True
            )
            original_markers = MARKER_PATTERN.findall(data["plain_text"])
            applied_changes: list[dict[str, Any]] = []
            for change in sorted_changes:
                corrected, replaced = _bounded_replace(
                    corrected, change["original"], change["corrected"]
                )
                # 仅保留实际发生替换的 change，避免展示与主字段不一致
                if replaced:
                    applied_changes.append(change)

            # marker 完整性复查：程序替换绝不允许改变 marker 序列，
            # 不一致则整体回退原文（与 LLM 输出层的计数 + 顺序校验同防线）
            if MARKER_PATTERN.findall(corrected) != original_markers:
                self._logger.warning(
                    f"Terminology replacement corrupted markers in {f} P{idx}; "
                    "falling back to original plain text"
                )
                corrected = data["plain_text"]
                applied_changes = []

            changes = applied_changes

            mark_corrected = corrected if changes else data["plain_text"]

            # 收集此段落的所有术语（去重）
            terms_found = list(dict.fromkeys(para_terms))

            result = {
                "paragraph_index": idx,
                "original": original,
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
        无法进行语义聚类，仅返回段落术语标签（来自规则降级提取），
        不生成 changes。
        """
        extractor = TermExtractor()
        results: list[dict[str, Any]] = []
        for data in all_para_data:
            terms = extractor.extract_terms_fallback(data["plain_text"])
            terms_found = list(dict.fromkeys(t.term for t in terms))
            results.append(
                {
                    "paragraph_index": data["para_index"],
                    "original": data["text"],
                    "corrected": data["plain_text"],
                    "terms": terms_found,
                    "changes": [],
                    "text": data["text"],
                    "plain_text": data["plain_text"],
                    "file_path": data.get("file_path", ""),
                }
            )
        return results

    def _check_no_llm(
        self,
        file_path: str,
        all_para_data: list[dict[str, Any]],
        operation_id: str = "",
    ) -> dict[str, Any]:
        """无 API Key 时降级为纯规则检查。"""
        results = self._build_results_no_llm(all_para_data)
        total_issues = sum(1 for r in results for c in r.get("changes", []))
        return {
            "results": results,
            "file_path": file_path,
            "total_issues": total_issues,
            "operation_id": operation_id,
        }

    async def _empty_terms(self, paragraph_index: int) -> list:
        return []

    def _empty_result(self, data: dict[str, Any]) -> dict[str, Any]:
        return {
            "paragraph_index": data["para_index"],
            "original": data["plain_text"],
            "corrected": data["plain_text"],
            "terms": [],
            "changes": [],
            "text": data["text"],
            "plain_text": data["plain_text"],
        }
