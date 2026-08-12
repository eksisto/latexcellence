"""
参考文献管理业务逻辑。

负责编排以下流程：
    1. 调用 LLM 识别参考文献格式规则
    2. 从文本或图片中提取结构化元数据
    3. 将元数据生成为规范的 BibTeX 代码
    4. 读写项目中的 .bib 文件
    5. 通过 DOI 查询获取论文元数据
"""

import base64
import io
import json
import os
from pathlib import Path
from typing import Any

from PIL import Image
from pydantic import ValidationError

from backend.core.logger import get_logger
from backend.core.workspace_manager import get_project_manager_for_workspace
from backend.core.llm_client import get_llm_client
from backend.core.config import get_config
from backend.core.prompts import get_prompt
from backend.modules.bib_manager.models import (
    BibEntry,
    DoiLookupResult,
    ExtractedMetadata,
    LatexCodeResult,
    ReferenceFormatResult,
    SavedFormatInfo,
)
from backend.modules.bib_manager.format_rules import detect_format
from backend.modules.bib_manager.bib_parser import (
    parse_bib_file,
    remove_entry_from_content,
    normalize_title,
    normalize_doi,
    BibParserError,
    extract_cite_key,
)
from backend.modules.bib_manager.style_registry import get_style_for_format
from backend.modules.bib_manager.provider_manager import get_provider_manager
from backend.utils.json_utils import retry_with_validation
from backend.utils.file_ops import atomic_write, safe_read
from backend.utils.path_guard import resolve_project_path

_MAX_DIMENSION = 2000
_SUPPORTED_FORMATS = {"image/png", "image/jpeg", "image/webp"}


def _safe_format_name(name: str) -> str:
    """仅保留字母数字与 _-，用于格式配置文件名（兼容中文等 Unicode 字母数字）。"""
    return "".join(c for c in name if c.isalnum() or c in "_-")


def _validate_format_name(name: str) -> str:
    """校验格式名可安全用作文件名，防止路径穿越。"""
    safe = _safe_format_name(name)
    if not safe or safe != name:
        raise ValueError("Invalid format name: contains unsafe characters")
    return safe


_LATEX_COMMENT_TEMPLATES = {
    "en": {
        "preamble": "Preamble package configuration to add in the preamble",
        "biblatex_print": (
            "Use the following command at the end of your document to "
            "generate the bibliography list"
        ),
        "bibtex_add": (
            r"Add the following after \begin{document} and before \end{document}:"
        ),
        "citation_example": "Citation example: {cmd}",
    },
    "zh": {
        "preamble": "以下宏包配置需要添加到导言区",
        "biblatex_print": "在文档末尾使用以下命令生成参考文献列表",
        "bibtex_add": (r"在 \begin{document} 之后、\end{document} 之前添加以下内容："),
        "citation_example": "引用示例：{cmd}",
    },
}


def _latex_comment_language(language: str) -> str:
    """根据 UI 语言选择 LaTeX 注释语言（zh / en）。"""
    normalized = (language or "").strip().lower()
    return "zh" if normalized.startswith(("zh", "chinese")) else "en"


class BibManagerService:
    def __init__(self):
        self._logger = get_logger("bib_manager")

    async def analyze_format(self, example: str) -> dict[str, Any]:
        """识别参考文献格式规则（确定性规则优先，LLM 兜底）。"""
        rule_result = detect_format(example)
        if rule_result:
            style_info = get_style_for_format(rule_result["format_name"])
            result = {
                **rule_result,
                "latex_backend": style_info.backend if style_info else None,
                "latex_package": style_info.package if style_info else None,
                "latex_bibliographystyle": (
                    style_info.bibliographystyle if style_info else None
                ),
                "latex_preamble": style_info.preamble if style_info else None,
            }
            self._logger.info(
                f"Format identified by rules: {rule_result['format_name']}"
            )
            return result

        cfg = get_config()
        if not cfg.llm.api_key:
            raise RuntimeError("No API key configured")

        llm = get_llm_client()
        system_prompt = get_prompt(
            "bib_manager", "format_analysis", reference_entry=example
        )
        messages = [
            {"role": "system", "content": system_prompt},
        ]

        try:
            validated: ReferenceFormatResult = await retry_with_validation(
                chat_fn=llm.chat,
                messages=messages,
                model_cls=ReferenceFormatResult,
                max_retries=3,
                logger_name="bib_manager",
                temperature=0.2,
                max_tokens=2048,
                response_format={"type": "json_object"},
            )
            result = validated.model_dump()

            # LaTeX 样式映射以注册表为唯一事实来源：LLM 输出不含样式字段，
            # 这里按格式名从注册表解析并写入响应；未命中时全部为 None。
            style_info = get_style_for_format(result.get("format_name", ""))
            result["latex_backend"] = style_info.backend if style_info else None
            result["latex_package"] = style_info.package if style_info else None
            result["latex_bibliographystyle"] = (
                style_info.bibliographystyle if style_info else None
            )
            result["latex_preamble"] = style_info.preamble if style_info else None
            if style_info:
                self._logger.info(
                    f"LaTeX style resolved from registry: "
                    f"{style_info.bibliographystyle or style_info.package}"
                )

            self._logger.info(f"Format identified: {validated.format_name}")
            return result
        except ValueError as e:
            self._logger.error(f"Format analysis failed: {e}")
            raise RuntimeError(f"Format analysis failed: {e}")
        except Exception as e:
            self._logger.error(f"Format analysis error: {e}")
            raise RuntimeError(f"Format analysis failed: {e}")

    def _build_extract_user_message(self, text: str, format_info: dict | None) -> str:
        """构建元数据提取的用户消息，列出要提取的具体字段名。"""
        all_fields = []
        if format_info:
            all_fields.extend(format_info.get("required_fields", []))
            all_fields.extend(format_info.get("optional_fields", []))
        field_names = (
            ", ".join(all_fields) if all_fields else "title, authors, year, journal"
        )

        fmt_name = format_info.get("format_name", "") if format_info else ""
        fmt_hint = f"This should follow the {fmt_name} format. " if fmt_name else ""

        return f"{fmt_hint}" f"FIELD NAMES: {field_names}\n\n" f"TEXT:\n{text}"

    async def extract_from_text(
        self, text: str, format_info: dict | None = None
    ) -> dict[str, Any]:
        """从文本提取文献元数据。"""
        cfg = get_config()
        if not cfg.llm.api_key:
            raise RuntimeError("No API key configured")

        llm = get_llm_client()
        user_content = self._build_extract_user_message(text, format_info)

        messages = [
            {
                "role": "system",
                "content": get_prompt("bib_manager", "metadata_extraction"),
            },
            {"role": "user", "content": user_content},
        ]

        try:
            validated: ExtractedMetadata = await retry_with_validation(
                chat_fn=llm.chat,
                messages=messages,
                model_cls=ExtractedMetadata,
                max_retries=3,
                logger_name="bib_manager",
                temperature=0.1,
                max_tokens=2048,
                response_format={"type": "json_object"},
            )
            fields = validated.fields
            self._logger.info(f"Metadata extracted: {len(fields)} fields from text")
            missing = self._check_missing_fields(fields, format_info)
            return {"fields": fields, "missing_fields": missing}
        except ValueError as e:
            self._logger.error(f"Metadata extraction failed: {e}")
            raise RuntimeError(f"Metadata extraction failed: {e}")
        except Exception as e:
            self._logger.error(f"Metadata extraction error: {e}")
            raise RuntimeError(f"Metadata extraction failed: {e}")

    async def extract_from_image(
        self,
        image_data: bytes,
        content_type: str,
        format_info: dict | None = None,
    ) -> dict[str, Any]:
        """从单张图片（截图）提取文献元数据。"""
        return await self.extract_from_images([(image_data, content_type)], format_info)

    async def extract_from_images(
        self,
        image_items: list[tuple[bytes, str]],
        format_info: dict | None = None,
    ) -> dict[str, Any]:
        """从多张图片（截图）提取文献元数据。"""
        cfg = get_config()
        if not cfg.llm.api_key:
            raise RuntimeError("No API key configured")

        all_fields = []
        if format_info:
            all_fields.extend(format_info.get("required_fields", []))
            all_fields.extend(format_info.get("optional_fields", []))
        field_names = (
            ", ".join(all_fields) if all_fields else "title, authors, year, journal"
        )

        fmt_name = format_info.get("format_name", "") if format_info else ""
        fmt_hint = f"This should follow the {fmt_name} format. " if fmt_name else ""

        image_parts: list[dict[str, Any]] = []
        for image_data, content_type in image_items:
            if content_type not in _SUPPORTED_FORMATS:
                raise ValueError(
                    f"Unsupported image format: {content_type}. "
                    f"Supported: PNG, JPEG, WebP"
                )
            img = self._preprocess_image(image_data)
            base64_image = self._to_base64(img)
            image_parts.append(
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/jpeg;base64,{base64_image}",
                        "detail": "high",
                    },
                }
            )

        llm = get_llm_client()
        user_content: list[dict[str, Any]] = [
            {
                "type": "text",
                "text": (
                    f"{fmt_hint}"
                    f"FIELD NAMES: {field_names}\n\n"
                    f"Extract the values for each field from the "
                    f"following {len(image_parts)} screenshot(s)."
                ),
            },
            *image_parts,
        ]

        messages = [
            {
                "role": "system",
                "content": get_prompt("bib_manager", "metadata_extraction"),
            },
            {"role": "user", "content": user_content},
        ]

        try:
            validated: ExtractedMetadata = await retry_with_validation(
                chat_fn=llm.chat,
                messages=messages,
                model_cls=ExtractedMetadata,
                max_retries=3,
                logger_name="bib_manager",
                temperature=0.1,
                max_tokens=2048,
                response_format={"type": "json_object"},
            )
            fields = validated.fields
            self._logger.info(
                f"Metadata extracted: {len(fields)} fields "
                f"from {len(image_parts)} image(s)"
            )
            missing = self._check_missing_fields(fields, format_info)
            return {"fields": fields, "missing_fields": missing}
        except ValueError as e:
            self._logger.error(f"Image metadata extraction failed: {e}")
            raise RuntimeError(f"Image metadata extraction failed: {e}")
        except Exception as e:
            self._logger.error(f"Image metadata extraction error: {e}")
            raise RuntimeError(f"Image metadata extraction failed: {e}")

    async def generate_bibtex(
        self,
        metadata: dict[str, Any],
        format_info: dict | None = None,
        existing_keys: list[str] | None = None,
    ) -> dict[str, Any]:
        """将元数据生成为 BibTeX 代码。"""
        cfg = get_config()
        if not cfg.llm.api_key:
            raise RuntimeError("No API key configured")

        llm = get_llm_client()

        entry_type = "article"
        if format_info and format_info.get("entry_type"):
            entry_type = format_info["entry_type"]

        existing_str = ""
        if existing_keys:
            existing_str = (
                f"\n\nExisting cite keys (avoid duplicates): {', '.join(existing_keys)}"
            )

        import json as _json

        metadata_str = _json.dumps(metadata, ensure_ascii=False, indent=2)

        messages = [
            {
                "role": "system",
                "content": get_prompt("bib_manager", "bibtex_generation"),
            },
            {
                "role": "user",
                "content": (
                    f'Generate a BibTeX entry with entry_type="{entry_type}" '
                    f"for the following metadata:{existing_str}\n\n"
                    f"METADATA:\n{metadata_str}"
                ),
            },
        ]

        try:
            validated: BibEntry = await retry_with_validation(
                chat_fn=llm.chat,
                messages=messages,
                model_cls=BibEntry,
                max_retries=3,
                logger_name="bib_manager",
                temperature=0.1,
                max_tokens=2048,
                response_format={"type": "json_object"},
            )
            self._logger.info(f"BibTeX generated: cite_key={validated.cite_key}")
            return validated.model_dump()
        except ValueError as e:
            self._logger.error(f"BibTeX generation failed: {e}")
            raise RuntimeError(f"BibTeX generation failed: {e}")
        except Exception as e:
            self._logger.error(f"BibTeX generation error: {e}")
            raise RuntimeError(f"BibTeX generation failed: {e}")

    def generate_latex_code(
        self,
        metadata: dict[str, Any],
        format_info: dict | None = None,
        bib_file_path: str | None = None,
        language: str = "English",
    ) -> dict[str, Any]:
        """根据格式信息生成包含正确样式的 LaTeX 参考文献代码。"""
        comments = _LATEX_COMMENT_TEMPLATES[_latex_comment_language(language)]
        # 从 format_info 或注册表获取 LaTeX 样式
        backend = None
        preamble = ""
        bibliographystyle = ""
        bib_path = bib_file_path or "references"

        if format_info:
            backend = format_info.get("latex_backend")
            preamble = format_info.get("latex_preamble") or ""
            bibliographystyle = format_info.get("latex_bibliographystyle") or ""
            fmt_name = format_info.get("format_name", "")
        else:
            fmt_name = ""

        # 注册表兜底
        if not bibliographystyle and fmt_name:
            style_info = get_style_for_format(fmt_name)
            if style_info:
                backend = backend or style_info.backend
                preamble = preamble or style_info.preamble
                bibliographystyle = style_info.bibliographystyle or ""

        # 如果仍然没有，从元数据中提取 cite_key 生成引用命令
        cite_key = metadata.get("cite_key", "")

        # 构建完整代码
        lines: list[str] = []

        if preamble:
            lines.append(f"% {comments['preamble']}")
            lines.append(preamble)
            lines.append("")

        if backend == "biblatex":
            # biblatex 工作流
            print_cmd = (
                format_info.get("print_command", "\\printbibliography")
                if format_info
                else "\\printbibliography"
            )
            lines.append(f"% {comments['biblatex_print']}")
            lines.append(print_cmd)
            if cite_key:
                cite_cmd = (
                    format_info.get("citation_command", "\\cite")
                    if format_info
                    else "\\cite"
                )
                citation = f"{cite_cmd}{{{cite_key}}}"
                lines.append("")
                lines.append(f"% {comments['citation_example'].format(cmd=citation)}")
        else:
            # BibTeX 工作流
            if bibliographystyle:
                lines.append(f"% {comments['bibtex_add']}")
                lines.append(f"\\bibliographystyle{{{bibliographystyle}}}")
            lines.append(f"\\bibliography{{{bib_path}}}")
            if cite_key:
                citation = f"\\cite{{{cite_key}}}"
                lines.append("")
                lines.append(f"% {comments['citation_example'].format(cmd=citation)}")

        full_code = "\n".join(lines)

        self._logger.info(
            f"LaTeX code generated: backend={backend}, " f"style={bibliographystyle}"
        )

        return {
            "preamble": preamble,
            "bibliographystyle": (
                f"\\bibliographystyle{{{bibliographystyle}}}"
                if bibliographystyle and backend != "biblatex"
                else ""
            ),
            "bibliography": (
                f"\\bibliography{{{bib_path}}}" if backend != "biblatex" else ""
            ),
            "cite_command": (f"\\cite{{{cite_key}}}" if cite_key else ""),
            "full_code": full_code,
        }

    def read_bib_file(self, file_path: str, workspace_id: str = "") -> dict[str, Any]:
        """读取 .bib 文件内容。"""
        pm = get_project_manager_for_workspace(workspace_id)
        if not pm.is_open:
            raise RuntimeError("No project opened")

        root = Path(pm.root_path)
        full_path = resolve_project_path(root, file_path, allowed_suffixes=(".bib",))
        if not full_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")

        content = safe_read(full_path)
        self._logger.info(f"Read bib file: {file_path} ({len(content)} chars)")
        return {"file_path": file_path, "content": content}

    def save_bib_entry(
        self, file_path: str, bibtex_code: str, cite_key: str, workspace_id: str = ""
    ) -> dict[str, Any]:
        """追加或更新 BibTeX 条目到 .bib 文件。"""
        pm = get_project_manager_for_workspace(workspace_id)
        if not pm.is_open:
            raise RuntimeError("No project opened")

        root = Path(pm.root_path)
        full_path = resolve_project_path(root, file_path, allowed_suffixes=(".bib",))
        if not full_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")

        content = safe_read(full_path)

        if cite_key in content:
            new_content = self._replace_bib_entry(content, cite_key, bibtex_code)
            action = "updated"
        else:
            content_stripped = content.rstrip()
            if content_stripped and not content_stripped.endswith("\n"):
                content_stripped += "\n"
            new_content = content_stripped + "\n" + bibtex_code.strip() + "\n"
            action = "appended"

        atomic_write(full_path, new_content)
        self._logger.info(f"BibTeX entry {action} in {file_path}: cite_key={cite_key}")
        return {"status": "ok", "action": action, "cite_key": cite_key}

    def list_bib_files(self, workspace_id: str = "") -> dict[str, Any]:
        """列出项目中的 .bib 文件。"""
        pm = get_project_manager_for_workspace(workspace_id)
        if not pm.is_open:
            raise RuntimeError("No project opened")

        root = Path(pm.root_path)
        bib_files = []
        for f in root.rglob("*.bib"):
            rel_path = f.relative_to(root).as_posix()
            bib_files.append(rel_path)

        self._logger.info(f"Found {len(bib_files)} .bib files in project")
        return {"bib_files": bib_files}

    def _check_missing_fields(
        self, fields: dict[str, str], format_info: dict | None
    ) -> list[str]:
        """只报告在 raw_observed_fields 中但值为空的必填字段。"""
        if not format_info:
            return []

        required = format_info.get("required_fields", [])
        observed = set(format_info.get("raw_observed_fields", []))
        missing = []
        for field in required:
            if field not in observed:
                continue
            value = fields.get(field)
            if value is None or str(value).strip() == "":
                missing.append(field)

        if missing:
            self._logger.warning(f"Missing required fields: {missing}")

        return missing

    def _replace_bib_entry(self, content: str, cite_key: str, new_bibtex: str) -> str:
        """在 .bib 文件中替换指定 cite_key 的条目。"""
        in_entry = False
        brace_count = 0
        lines = content.split("\n")
        result = []
        entry_lines = []
        entry_start = -1

        for i, line in enumerate(lines):
            stripped = line.strip()
            if not in_entry:
                if stripped.startswith("@"):
                    key = extract_cite_key(stripped)
                    if key == cite_key:
                        in_entry = True
                        brace_count = line.count("{") - line.count("}")
                        entry_start = i
                        entry_lines = [line]
                        continue
                result.append(line)
            else:
                entry_lines.append(line)
                brace_count += line.count("{") - line.count("}")
                if brace_count <= 0:
                    result.append(new_bibtex.strip())
                    in_entry = False
                    continue

        if in_entry and entry_start >= 0:
            for _ in range(len(entry_lines)):
                if result:
                    result.pop()
            result.append(new_bibtex.strip())

        return "\n".join(result)

    def _get_format_dir(self) -> Path:
        """获取格式配置文件目录，不存在则创建。"""
        cfg = get_config()
        fmt_dir = Path(cfg.features.bib_manager.format_configs_dir)
        if not fmt_dir.is_absolute():
            fmt_dir = Path.cwd() / fmt_dir
        fmt_dir.mkdir(parents=True, exist_ok=True)
        return fmt_dir

    def list_formats(self) -> dict[str, Any]:
        """列出所有保存的格式配置。"""
        fmt_dir = self._get_format_dir()
        formats = []
        for f in sorted(fmt_dir.glob("*.json")):
            name = f.stem
            try:
                with open(f, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                formats.append(
                    {
                        "name": name,
                        "format_name": data.get("format_name", name),
                        "entry_type": data.get("entry_type", ""),
                    }
                )
            except Exception:
                formats.append({"name": name, "format_name": name, "entry_type": ""})
        return {"formats": formats}

    def load_format(self, name: str) -> dict[str, Any] | None:
        """加载指定格式配置。"""
        fmt_dir = self._get_format_dir()
        file_path = fmt_dir / f"{_validate_format_name(name)}.json"
        if not file_path.exists():
            return None
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        # 兼容旧数据：保存的配置未携带 LaTeX 样式字段时，按格式名从注册表补全
        if not any(
            data.get(key)
            for key in (
                "latex_backend",
                "latex_package",
                "latex_bibliographystyle",
                "latex_preamble",
            )
        ):
            style_info = get_style_for_format(data.get("format_name", ""))
            if style_info:
                data["latex_backend"] = style_info.backend
                data["latex_package"] = style_info.package
                data["latex_bibliographystyle"] = style_info.bibliographystyle
                data["latex_preamble"] = style_info.preamble
        self._logger.info(f"Loaded format: {name}")
        return data

    def save_format(self, name: str, format_info: dict[str, Any]) -> dict[str, Any]:
        """保存格式配置为 JSON 文件（保存前统一校验字段与 LaTeX 内容）。"""
        fmt_dir = self._get_format_dir()
        safe_name = _safe_format_name(name)
        if not safe_name:
            safe_name = "unnamed"
        try:
            validated = SavedFormatInfo.model_validate(format_info)
        except ValidationError as e:
            raise ValueError(f"Invalid format info: {e}") from e
        file_path = fmt_dir / f"{safe_name}.json"
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(validated.model_dump(), f, indent=2, ensure_ascii=False)
        self._logger.info(f"Saved format: {safe_name}")
        return {"status": "ok", "name": safe_name}

    def delete_format(self, name: str) -> bool:
        """删除指定格式配置。"""
        fmt_dir = self._get_format_dir()
        file_path = fmt_dir / f"{_validate_format_name(name)}.json"
        if not file_path.exists():
            return False
        os.remove(file_path)
        self._logger.info(f"Deleted format: {name}")
        return True

    def _preprocess_image(self, image_data: bytes) -> Image.Image:
        """预处理图片。"""
        max_size_mb = 10
        max_size_bytes = max_size_mb * 1024 * 1024

        if len(image_data) > max_size_bytes:
            raise ValueError(
                f"Image too large: {len(image_data) / 1024 / 1024:.1f}MB. Maximum: {max_size_mb}MB"
            )

        img = Image.open(io.BytesIO(image_data))

        width, height = img.size
        if width > _MAX_DIMENSION or height > _MAX_DIMENSION:
            ratio = min(_MAX_DIMENSION / width, _MAX_DIMENSION / height)
            new_width = int(width * ratio)
            new_height = int(height * ratio)
            img = img.resize((new_width, new_height), Image.LANCZOS)
            self._logger.info(f"Resized image: {(width, height)} -> {img.size}")

        return img

    def _to_base64(self, img: Image.Image) -> str:
        """将 PIL Image 转为 base64 字符串。"""
        buffer = io.BytesIO()
        if img.mode in ("RGBA", "LA", "P"):
            img = img.convert("RGB")
        img.save(buffer, format="JPEG", quality=85)
        return base64.b64encode(buffer.getvalue()).decode("utf-8")

    async def lookup_doi(self, doi: str) -> dict[str, Any]:
        """通过 DOI 查询多数据源聚合元数据。

        路由策略：
        - arXiv DOI（10.48550/arXiv.*）→ ArxivProvider
        - 普通 DOI → Crossref（主索引）→ 补全层按优先级降级
        """
        manager = get_provider_manager()
        result = await manager.lookup_doi(doi)
        return result.model_dump()

    async def generate_bibtex_from_doi(
        self,
        doi: str,
        format_info: dict | None = None,
        existing_keys: list[str] | None = None,
    ) -> dict[str, Any]:
        """通过 DOI 查询元数据并生成 BibTeX 代码。"""
        # 1. 查询 Crossref API 获取元数据
        metadata = await self.lookup_doi(doi)

        # 2. 调用 LLM 生成 BibTeX
        return await self.generate_bibtex(metadata, format_info, existing_keys)

    # ── 文献库管理 ──────────────────────────────────────────────────────────

    def parse_entries(self, file_path: str, workspace_id: str = "") -> dict[str, Any]:
        """解析 .bib 文件为结构化条目列表。"""
        pm = get_project_manager_for_workspace(workspace_id)
        if not pm.is_open:
            raise RuntimeError("No project opened")

        root = Path(pm.root_path)
        full_path = resolve_project_path(root, file_path, allowed_suffixes=(".bib",))
        if not full_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")

        content = safe_read(full_path)
        entries = parse_bib_file(content)
        result = [
            {k: v for k, v in e.items() if not k.startswith("_")} for e in entries
        ]
        self._logger.info(f"Parsed {len(result)} entries from {file_path}")
        return {"file_path": file_path, "entries": result, "total": len(result)}

    def detect_duplicates(
        self, file_path: str | None = None, workspace_id: str = ""
    ) -> dict[str, Any]:
        """检测 .bib 文件中的重复条目。"""
        pm = get_project_manager_for_workspace(workspace_id)
        if not pm.is_open:
            raise RuntimeError("No project opened")

        root = Path(pm.root_path)

        # 收集所有条目
        all_entries: list[dict[str, Any]] = []
        if file_path:
            bib_files = [file_path]
        else:
            bib_result = self.list_bib_files(workspace_id)
            bib_files = bib_result["bib_files"]

        for bf in bib_files:
            try:
                parsed = self.parse_entries(bf, workspace_id)
                for entry in parsed["entries"]:
                    entry["_file_path"] = bf
                    all_entries.append(entry)
            except Exception:
                continue

        if len(all_entries) < 2:
            return {
                "duplicates": [],
                "total_duplicates": 0,
                "total_entries": len(all_entries),
            }

        duplicates = self._find_duplicates(all_entries)
        self._logger.info(
            f"Duplicate detection: {len(duplicates)} groups found "
            f"in {len(all_entries)} entries"
        )
        return {
            "duplicates": duplicates,
            "total_duplicates": len(duplicates),
            "total_entries": len(all_entries),
        }

    def _find_duplicates(self, entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """多策略重复检测（并查集合并传递重复组）。"""
        n = len(entries)

        # 并查集
        parent = list(range(n))

        def find(x: int) -> int:
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(x: int, y: int) -> None:
            px, py = find(x), find(y)
            if px < py:
                parent[py] = px
            elif py < px:
                parent[px] = py

        for i in range(n):
            for j in range(i + 1, n):
                reason = self._classify_duplicate(entries[i], entries[j])
                if reason:
                    union(i, j)

        # 按根节点分组
        root_entries: dict[int, list[int]] = {}
        for i in range(n):
            root = find(i)
            if root not in root_entries:
                root_entries[root] = []
            root_entries[root].append(i)

        # 构建结果（仅保留 ≥2 个成员的组）
        groups: list[dict[str, Any]] = []
        for root, indices in root_entries.items():
            if len(indices) < 2:
                continue
            a = entries[indices[0]]
            b = entries[indices[1]]
            reason = self._classify_duplicate(a, b) or {
                "reason": "unknown",
                "confidence": "medium",
            }

            groups.append(
                {
                    "reason": reason["reason"],
                    "confidence": reason["confidence"],
                    "entries": [
                        {
                            "cite_key": entries[i]["cite_key"],
                            "file_path": entries[i].get("_file_path", ""),
                            "title": entries[i].get("fields", {}).get("title", ""),
                            "doi": entries[i].get("fields", {}).get("doi", ""),
                            "year": entries[i].get("fields", {}).get("year", ""),
                        }
                        for i in sorted(indices, key=lambda i: entries[i]["cite_key"])
                    ],
                }
            )

        groups.sort(key=lambda g: g["entries"][0]["cite_key"])

        return groups

    def _classify_duplicate(
        self, a: dict[str, Any], b: dict[str, Any]
    ) -> dict[str, str] | None:
        """判断两个条目是否为重复。"""
        from difflib import SequenceMatcher

        fields_a = a.get("fields", {})
        fields_b = b.get("fields", {})

        # 策略 1：DOI 严格匹配
        doi_a = normalize_doi(fields_a.get("doi", ""))
        doi_b = normalize_doi(fields_b.get("doi", ""))
        if doi_a and doi_b and doi_a == doi_b:
            return {"reason": "doi", "confidence": "high"}

        # 策略 2：标题相似度
        title_a = normalize_title(fields_a.get("title", ""))
        title_b = normalize_title(fields_b.get("title", ""))
        if title_a and title_b and len(title_a) > 10 and len(title_b) > 10:
            ratio = SequenceMatcher(None, title_a, title_b).ratio()
            if ratio >= 0.85:
                return {
                    "reason": "title",
                    "confidence": "high" if ratio >= 0.95 else "medium",
                }

        # 策略 3：cite_key 完全相同
        if a["cite_key"] == b["cite_key"]:
            return {"reason": "cite_key", "confidence": "high"}

        return None

    def delete_entry(
        self, file_path: str, cite_key: str, workspace_id: str = ""
    ) -> dict[str, Any]:
        """从 .bib 文件中删除指定条目。"""
        pm = get_project_manager_for_workspace(workspace_id)
        if not pm.is_open:
            raise RuntimeError("No project opened")

        root = Path(pm.root_path)
        full_path = resolve_project_path(root, file_path, allowed_suffixes=(".bib",))
        if not full_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")

        content = safe_read(full_path)
        new_content = remove_entry_from_content(content, cite_key)
        atomic_write(full_path, new_content)
        self._logger.info(f"Deleted entry {cite_key} from {file_path}")
        return {"status": "ok", "cite_key": cite_key}

    def update_entry(
        self, file_path: str, cite_key: str, bibtex_code: str, workspace_id: str = ""
    ) -> dict[str, Any]:
        """更新 .bib 文件中的指定条目。"""
        pm = get_project_manager_for_workspace(workspace_id)
        if not pm.is_open:
            raise RuntimeError("No project opened")

        root = Path(pm.root_path)
        full_path = resolve_project_path(root, file_path, allowed_suffixes=(".bib",))
        if not full_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")

        content = safe_read(full_path)
        new_content = self._replace_bib_entry(content, cite_key, bibtex_code.strip())
        atomic_write(full_path, new_content)
        self._logger.info(f"Updated entry {cite_key} in {file_path}")
        return {"status": "ok", "cite_key": cite_key}

    def get_entry_stats(self, file_path: str, workspace_id: str = "") -> dict[str, Any]:
        """获取 .bib 文件的条目统计信息。"""
        parsed = self.parse_entries(file_path, workspace_id)
        entries = parsed["entries"]
        by_type: dict[str, int] = {}
        by_year: dict[str, int] = {}
        with_doi = 0
        without_doi = 0

        for entry in entries:
            et = entry.get("entry_type", "misc")
            by_type[et] = by_type.get(et, 0) + 1

            yr = entry.get("fields", {}).get("year", "")
            yr_clean = yr.strip()[:4]
            if yr_clean and yr_clean.isdigit():
                by_year[yr_clean] = by_year.get(yr_clean, 0) + 1
            else:
                by_year["unknown"] = by_year.get("unknown", 0) + 1

            doi_val = normalize_doi(entry.get("fields", {}).get("doi", ""))
            if doi_val:
                with_doi += 1
            else:
                without_doi += 1

        self._logger.info(
            f"Stats for {file_path}: {len(entries)} entries, "
            f"{len(by_type)} types, {len(by_year)} years"
        )
        return {
            "total": len(entries),
            "by_type": by_type,
            "by_year": by_year,
            "with_doi": with_doi,
            "without_doi": without_doi,
        }
