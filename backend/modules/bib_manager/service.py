"""
参考文献管理业务逻辑。

负责编排以下流程：
    1. 调用 LLM 识别参考文献格式规则
    2. 从文本或图片中提取结构化元数据
    3. 将元数据生成为规范的 BibTeX 代码
    4. 读写项目中的 .bib 文件
"""

import base64
import io
import json
import os
from pathlib import Path
from typing import Any, Optional

from PIL import Image
from pydantic import BaseModel

from backend.core.logger import get_logger
from backend.core.workspace_manager import get_project_manager_for_workspace
from backend.core.llm_client import get_llm_client
from backend.core.config import get_config
from backend.modules.bib_manager.models import (
    BibEntry,
    ExtractedMetadata,
    ReferenceFormatResult,
)
from backend.utils.json_utils import retry_with_validation
from backend.utils.file_ops import atomic_write, safe_read


_FORMAT_ANALYSIS_PROMPT = (
    "You are a bibliography format analyzer.\n\n"
    "## Input\n"
    "A single formatted reference entry:\n"
    "<reference>\n{reference_entry}\n</reference>\n\n"
    "## Task\n"
    "1. Identify the citation format (GB/T 7714-2015, APA 7th, IEEE, MLA 9th, "
    "Chicago, Vancouver, or unknown).\n"
    "2. Map it to the most appropriate BibTeX entry type "
    "(article, book, inproceedings, incollection, phdthesis, mastersthesis, "
    "techreport, misc, etc.).\n"
    "3. Extract the formatting rules observed in this entry.\n\n"
    "## Guidelines\n"
    "- If the format cannot be determined with confidence, set format_name to "
    '"unknown" and confidence to "low".\n'
    "- required_fields / optional_fields must be specific to the inferred "
    "entry_type, not generic.\n"
    "- field_descriptions should cover ALL fields actually present in the "
    "entry (e.g., editor, address, isbn, edition, language), not just the "
    "example keys.\n"
    "- format_description should cover: field ordering, punctuation between "
    "fields, author name style, title capitalization, and any special markers "
    "(e.g., [J], [M], DOI placement).\n\n"
    "## Output\n"
    "Respond with JSON only. No markdown fences, no commentary.\n\n"
    "{{\n"
    '  "format_name": "GB/T 7714-2015 | APA 7th | IEEE | MLA 9th | Chicago | unknown",\n'
    '  "confidence": "high | medium | low",\n'
    '  "entry_type": "article | book | inproceedings | ...",\n'
    '  "document_type_marker": "[J] | [M] | [C] | [D] | null (for non-GB/T formats)",\n'
    '  "required_fields": ["author", "title", ...],\n'
    '  "optional_fields": ["doi", "url", ...],\n'
    '  "field_descriptions": {{\n'
    '    "<field_name>": "<the meaning of this field in this entry and its formatting requirements>",\n'
    '    ...\n'
    '  }},\n'
    '  "format_description": "Describe in order: author format → title → document type marker → journal/publication information → year/volume/issue/pages → DOI/URL, etc.",\n'
    '  "raw_observed_fields": ["the list of fields that actually appear in the entry, for validation"]\n'
    "}}"
)

_METADATA_EXTRACTION_PROMPT = (
    "You are a bibliography metadata extractor. "
    "The user will provide a block of text (or an image) containing publication metadata, "
    "along with a list of specific fields to extract.\n\n"
    "Your task is to extract the values for each requested field from the input.\n\n"
    "Guidelines:\n"
    "- Extract ONLY the fields listed in the FIELD NAMES section of the user message\n"
    "- Clean author names: use 'and' to separate authors, remove extra spaces\n"
    "- Standardize the title: capitalize only proper nouns if in English\n"
    "- For year fields, output only digits as a string\n"
    "- Leave a field as empty string if not found, do NOT guess or fabricate\n"
    "- Output each value as a plain string\n\n"
    "Respond with JSON only, no markdown fences, no extra text.\n\n"
    "JSON format:\n"
    "{{\n"
    '  "fields": {{\n'
    '    "<field_name_1>": "<extracted value>",\n'
    '    "<field_name_2>": "<extracted value>"\n'
    '  }}\n'
    "}}"
)

_BIBTEX_GENERATION_PROMPT = (
    "You are a BibTeX code generator. "
    "The user will provide structured publication metadata and format requirements.\n\n"
    "Your task is to generate a correct, well-formatted BibTeX entry.\n\n"
    "Guidelines:\n"
    "- Use the specified entry_type (article, inproceedings, book, etc.)\n"
    "- Generate an appropriate cite_key in the format: "
    "firstAuthorLastName + year + firstMeaningfulKeyword (all lowercase)\n"
    "  Example: devlin2018bert, krizhevsky2012imagenet\n"
    "- Include all available fields, use empty string for missing required fields\n"
    "- Each field should be properly quoted with curly braces: title = {{...}},\n"
    "- Preserve LaTeX special characters in titles (escape % & _ # if needed)\n"
    "- Format output as clean, indented BibTeX code\n"
    "- Do NOT include fields that have no value (empty string) as optional fields\n"
    "- But DO include required fields even if empty\n"
    "- Avoid duplicate cite_keys: if existing_keys is provided, adjust the cite_key\n\n"
    "Respond with JSON only, no markdown fences, no extra text.\n\n"
    "JSON format:\n"
    "{{\n"
    '  "cite_key": "<generated cite key>",\n'
    '  "entry_type": "<BibTeX entry type>",\n'
    '  "fields": {{\n'
    '    "author": "<author1 and author2>",\n'
    '    "title": "<title>",\n'
    '    "journal": "<journal>",\n'
    '    "year": "<year>"\n'
    '  }},\n'
    '  "raw_bibtex": "<complete formatted BibTeX code as a single string with \\\\n line breaks>"\n'
    "}}"
)

_MAX_DIMENSION = 2000
_SUPPORTED_FORMATS = {"image/png", "image/jpeg", "image/webp"}


class BibManagerService:
    def __init__(self):
        self._logger = get_logger("bib_manager")

    async def analyze_format(self, example: str) -> dict[str, Any]:
        """识别参考文献格式规则。"""
        cfg = get_config()
        if not cfg.llm.api_key:
            raise RuntimeError("No API key configured")

        llm = get_llm_client()
        system_prompt = _FORMAT_ANALYSIS_PROMPT.format(reference_entry=example)
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
            self._logger.info(f"Format identified: {validated.format_name}")
            return validated.model_dump()
        except ValueError as e:
            self._logger.error(f"Format analysis failed: {e}")
            raise RuntimeError(f"Failed to analyze format: {e}")
        except Exception as e:
            self._logger.error(f"Format analysis error: {e}")
            raise RuntimeError(f"Format analysis failed: {e}")

    def _build_extract_user_message(
        self, text: str, format_info: Optional[dict]
    ) -> str:
        """构建元数据提取的用户消息，列出要提取的具体字段名。"""
        all_fields = []
        if format_info:
            all_fields.extend(format_info.get("required_fields", []))
            all_fields.extend(format_info.get("optional_fields", []))
        field_names = ", ".join(
            all_fields) if all_fields else "title, authors, year, journal"

        fmt_name = format_info.get("format_name", "") if format_info else ""
        fmt_hint = f"This should follow the {fmt_name} format. " if fmt_name else ""

        return (
            f"{fmt_hint}"
            f"FIELD NAMES: {field_names}\n\n"
            f"TEXT:\n{text}"
        )

    async def extract_from_text(
        self, text: str, format_info: Optional[dict] = None
    ) -> dict[str, Any]:
        """从文本提取文献元数据。"""
        cfg = get_config()
        if not cfg.llm.api_key:
            raise RuntimeError("No API key configured")

        llm = get_llm_client()
        user_content = self._build_extract_user_message(text, format_info)

        messages = [
            {"role": "system", "content": _METADATA_EXTRACTION_PROMPT},
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
                f"Metadata extracted: {len(fields)} fields from text"
            )
            missing = self._check_missing_fields(fields, format_info)
            return {"fields": fields, "missing_fields": missing}
        except ValueError as e:
            self._logger.error(f"Metadata extraction failed: {e}")
            raise RuntimeError(f"Failed to extract metadata: {e}")
        except Exception as e:
            self._logger.error(f"Metadata extraction error: {e}")
            raise RuntimeError(f"Metadata extraction failed: {e}")

    async def extract_from_image(
        self,
        image_data: bytes,
        content_type: str,
        format_info: Optional[dict] = None,
    ) -> dict[str, Any]:
        """从图片（截图）提取文献元数据。"""
        cfg = get_config()
        if not cfg.llm.api_key:
            raise RuntimeError("No API key configured")

        if content_type not in _SUPPORTED_FORMATS:
            raise ValueError(
                f"Unsupported image format: {content_type}. Supported: PNG, JPEG, WebP"
            )

        img = self._preprocess_image(image_data)
        base64_image = self._to_base64(img)

        all_fields = []
        if format_info:
            all_fields.extend(format_info.get("required_fields", []))
            all_fields.extend(format_info.get("optional_fields", []))
        field_names = ", ".join(
            all_fields) if all_fields else "title, authors, year, journal"

        fmt_name = format_info.get("format_name", "") if format_info else ""
        fmt_hint = f"This should follow the {fmt_name} format. " if fmt_name else ""

        llm = get_llm_client()
        messages = [
            {"role": "system", "content": _METADATA_EXTRACTION_PROMPT},
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            f"{fmt_hint}"
                            f"FIELD NAMES: {field_names}\n\n"
                            f"Extract the values for each field from this screenshot."
                        ),
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{base64_image}",
                            "detail": "high",
                        },
                    },
                ],
            },
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
                f"Metadata extracted: {len(fields)} fields from image"
            )
            missing = self._check_missing_fields(fields, format_info)
            return {"fields": fields, "missing_fields": missing}
        except ValueError as e:
            self._logger.error(f"Image metadata extraction failed: {e}")
            raise RuntimeError(f"Failed to extract metadata from image: {e}")
        except Exception as e:
            self._logger.error(f"Image metadata extraction error: {e}")
            raise RuntimeError(f"Metadata extraction from image failed: {e}")

    async def generate_bibtex(
        self,
        metadata: dict[str, Any],
        format_info: Optional[dict] = None,
        existing_keys: Optional[list[str]] = None,
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
            {"role": "system", "content": _BIBTEX_GENERATION_PROMPT},
            {
                "role": "user",
                "content": (
                    f"Generate a BibTeX entry with entry_type=\"{entry_type}\" "
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
            self._logger.info(
                f"BibTeX generated: cite_key={validated.cite_key}")
            return validated.model_dump()
        except ValueError as e:
            self._logger.error(f"BibTeX generation failed: {e}")
            raise RuntimeError(f"Failed to generate BibTeX: {e}")
        except Exception as e:
            self._logger.error(f"BibTeX generation error: {e}")
            raise RuntimeError(f"BibTeX generation failed: {e}")

    def read_bib_file(self, file_path: str, workspace_id: str = "") -> dict[str, Any]:
        """读取 .bib 文件内容。"""
        pm = get_project_manager_for_workspace(workspace_id)
        if not pm.is_open:
            raise RuntimeError("No project opened")

        root = Path(pm.root_path)
        full_path = root / file_path
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
        full_path = root / file_path
        if not full_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")

        content = safe_read(full_path)

        if cite_key in content:
            new_content = self._replace_bib_entry(
                content, cite_key, bibtex_code)
            action = "updated"
        else:
            content_stripped = content.rstrip()
            if content_stripped and not content_stripped.endswith("\n"):
                content_stripped += "\n"
            new_content = content_stripped + "\n" + bibtex_code.strip() + "\n"
            action = "appended"

        atomic_write(full_path, new_content)
        self._logger.info(
            f"BibTeX entry {action} in {file_path}: cite_key={cite_key}"
        )
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
        self, fields: dict[str, str], format_info: Optional[dict]
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

    def _replace_bib_entry(
        self, content: str, cite_key: str, new_bibtex: str
    ) -> str:
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
                    match_key = stripped.split("{", 1)
                    if len(match_key) > 1:
                        key = match_key[1].rstrip(",").strip()
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
                formats.append({
                    "name": name,
                    "format_name": data.get("format_name", name),
                    "entry_type": data.get("entry_type", ""),
                })
            except Exception:
                formats.append(
                    {"name": name, "format_name": name, "entry_type": ""})
        return {"formats": formats}

    def load_format(self, name: str) -> Optional[dict[str, Any]]:
        """加载指定格式配置。"""
        fmt_dir = self._get_format_dir()
        file_path = fmt_dir / f"{name}.json"
        if not file_path.exists():
            return None
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self._logger.info(f"Loaded format: {name}")
        return data

    def save_format(self, name: str, format_info: dict[str, Any]) -> dict[str, Any]:
        """保存格式配置为 JSON 文件。"""
        fmt_dir = self._get_format_dir()
        safe_name = "".join(c for c in name if c.isalnum() or c in "_-")
        if not safe_name:
            safe_name = "unnamed"
        file_path = fmt_dir / f"{safe_name}.json"
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(format_info, f, indent=2, ensure_ascii=False)
        self._logger.info(f"Saved format: {safe_name}")
        return {"status": "ok", "name": safe_name}

    def delete_format(self, name: str) -> bool:
        """删除指定格式配置。"""
        fmt_dir = self._get_format_dir()
        file_path = fmt_dir / f"{name}.json"
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
            self._logger.info(
                f"Resized image: {(width, height)} -> {img.size}")

        return img

    def _to_base64(self, img: Image.Image) -> str:
        """将 PIL Image 转为 base64 字符串。"""
        buffer = io.BytesIO()
        if img.mode in ("RGBA", "LA", "P"):
            img = img.convert("RGB")
        img.save(buffer, format="JPEG", quality=85)
        return base64.b64encode(buffer.getvalue()).decode("utf-8")
