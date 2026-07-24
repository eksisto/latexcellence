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
from typing import Any

from PIL import Image
from pydantic import BaseModel

from backend.core.logger import get_logger
from backend.core.workspace_manager import get_project_manager_for_workspace
from backend.core.llm_client import get_llm_client
from backend.core.config import get_config
from backend.core.prompts import get_prompt
from backend.modules.bib_manager.models import (
    BibEntry,
    ExtractedMetadata,
    LatexCodeResult,
    ReferenceFormatResult,
)
from backend.modules.bib_manager.style_registry import get_style_for_format
from backend.utils.json_utils import retry_with_validation
from backend.utils.file_ops import atomic_write, safe_read
from backend.utils.path_guard import resolve_project_path


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
        system_prompt = get_prompt("bib_manager", "format_analysis", reference_entry=example)
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

            # 如果 LLM 未提供 LaTeX 样式字段，从注册表补全
            if not result.get("latex_bibliographystyle"):
                style_info = get_style_for_format(result.get("format_name", ""))
                if style_info:
                    result["latex_backend"] = style_info.backend
                    result["latex_package"] = style_info.package
                    result["latex_bibliographystyle"] = style_info.bibliographystyle
                    result["latex_preamble"] = style_info.preamble
                    self._logger.info(
                        f"LaTeX style filled from registry: "
                        f"{style_info.bibliographystyle}"
                    )

            self._logger.info(f"Format identified: {validated.format_name}")
            return result
        except ValueError as e:
            self._logger.error(f"Format analysis failed: {e}")
            raise RuntimeError(f"Format analysis failed: {e}")
        except Exception as e:
            self._logger.error(f"Format analysis error: {e}")
            raise RuntimeError(f"Format analysis failed: {e}")

    def _build_extract_user_message(
        self, text: str, format_info: dict | None
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
        self, text: str, format_info: dict | None = None
    ) -> dict[str, Any]:
        """从文本提取文献元数据。"""
        cfg = get_config()
        if not cfg.llm.api_key:
            raise RuntimeError("No API key configured")

        llm = get_llm_client()
        user_content = self._build_extract_user_message(text, format_info)

        messages = [
            {"role": "system", "content": get_prompt("bib_manager", "metadata_extraction")},
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
            {"role": "system", "content": get_prompt("bib_manager", "metadata_extraction")},
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
            {"role": "system", "content": get_prompt("bib_manager", "bibtex_generation")},
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
            raise RuntimeError(f"BibTeX generation failed: {e}")
        except Exception as e:
            self._logger.error(f"BibTeX generation error: {e}")
            raise RuntimeError(f"BibTeX generation failed: {e}")

    def generate_latex_code(
        self,
        metadata: dict[str, Any],
        format_info: dict | None = None,
        bib_file_path: str | None = None,
    ) -> dict[str, Any]:
        """根据格式信息生成包含正确样式的 LaTeX 参考文献代码。"""
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
            lines.append(f"% 需要添加到导言区的宏包配置")
            lines.append(preamble)
            lines.append("")

        if backend == "biblatex":
            # biblatex 工作流
            print_cmd = format_info.get(
                "print_command", "\\printbibliography"
            ) if format_info else "\\printbibliography"
            lines.append(f"% 在文档末尾使用以下命令生成参考文献列表")
            lines.append(print_cmd)
            if cite_key:
                cite_cmd = format_info.get(
                    "citation_command", "\\cite"
                ) if format_info else "\\cite"
                lines.append("")
                lines.append(f"% 引用示例：{cite_cmd}{{{cite_key}}}")
        else:
            # BibTeX 工作流
            if bibliographystyle:
                lines.append(f"% 在 \\begin{{document}} 之后、\\end{{document}} 之前添加：")
                lines.append(f"\\bibliographystyle{{{bibliographystyle}}}")
            lines.append(f"\\bibliography{{{bib_path}}}")
            if cite_key:
                lines.append("")
                lines.append(f"% 引用示例：\\cite{{{cite_key}}}")

        full_code = "\n".join(lines)

        self._logger.info(
            f"LaTeX code generated: backend={backend}, "
            f"style={bibliographystyle}"
        )

        return {
            "preamble": preamble,
            "bibliographystyle": (
                f"\\bibliographystyle{{{bibliographystyle}}}"
                if bibliographystyle and backend != "biblatex" else ""
            ),
            "bibliography": (
                f"\\bibliography{{{bib_path}}}"
                if backend != "biblatex" else ""
            ),
            "cite_command": (
                f"\\cite{{{cite_key}}}" if cite_key else ""
            ),
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

    def load_format(self, name: str) -> dict[str, Any] | None:
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
