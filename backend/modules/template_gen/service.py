"""
模板生成业务逻辑。

根据用户描述生成 LaTeX 模板，包含：
    - documentclass + preamble（包导入、配置）
    - body 结构（section 骨架和推荐内容）
    - 使用说明
"""

from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel

from backend.core.logger import get_logger
from backend.core.llm_client import get_llm_client
from backend.core.config import get_config
from backend.core.prompts import get_prompt
from backend.utils.json_utils import retry_with_validation


class SectionSkeleton(BaseModel):
    """模板节骨架"""
    section: str
    suggested_content: str = ""


class TemplateGenResult(BaseModel):
    """LLM 模板生成输出——对应 DEVELOPMENT.md §2.1 JSON 协议"""
    template_name: str
    preamble: str = ""
    body_structure: list[SectionSkeleton] = []
    packages: list[str] = []
    explanation: str = ""


class TemplateGenService:
    """
    模板生成服务。

    用法：
        svc = TemplateGenService()
        result = await svc.generate_template("a CVPR-style paper")
    """

    def __init__(self):
        self._logger = get_logger("template_gen")

    async def generate_template(
        self, description: str, llm=None
    ) -> dict[str, Any]:
        """
        根据用户描述生成 LaTeX 模板。

        参数：
            description: 用户对所需模板的描述
            llm: 可选的 LLM 客户端（测试用）

        返回：
            包含 template_name / preamble / body_structure / packages / explanation 的 dict
        """
        cfg = get_config()

        if not cfg.llm.api_key:
            self._logger.warning(
                "No API key configured, returning empty template")
            return {
                "template_name": "empty",
                "preamble": r"\documentclass{article}",
                "body_structure": [],
                "packages": [],
                "explanation": "No API key configured.",
            }

        if llm is None:
            llm = get_llm_client()
        messages = [
            {"role": "system", "content": get_prompt(
                "template_gen", "system")},
            {
                "role": "user",
                "content": (
                    f"Please generate a LaTeX template for the following requirement:\n\n"
                    f"{description}\n\n"
                    f"Make sure the template is complete and ready to use."
                ),
            },
        ]

        try:
            validated: TemplateGenResult = await retry_with_validation(
                chat_fn=llm.chat,
                messages=messages,
                model_cls=TemplateGenResult,
                max_retries=3,
                logger_name="template_gen",
                temperature=0.3,
                max_tokens=2048,
                response_format={"type": "json_object"},
            )

            return validated.model_dump()

        except ValueError as e:
            self._logger.error(f"Template generation failed: {e}")
            raise RuntimeError(f"Template generation failed: {e}")
        except Exception as e:
            self._logger.error(f"Template generation error: {e}")
            raise RuntimeError(f"Template generation failed: {e}")

    async def save_template(self, template: dict[str, Any]) -> dict[str, Any]:
        """
        将生成的模板保存为 .tex 文件。

        参数：
            template: generate_template 返回的完整结果

        返回：
            { "saved": true, "file_path": "..." }
        """
        cfg = get_config()
        output_dir = Path(cfg.features.template_gen.output_dir)
        if not output_dir.is_absolute():
            output_dir = Path.cwd() / output_dir
        output_dir.mkdir(parents=True, exist_ok=True)

        name = template.get("template_name", "untitled")
        safe_name = "".join(
            c if c.isalnum() or c in "-_" else "_" for c in name)
        file_path = output_dir / f"{safe_name}.tex"

        content = self._assemble_template(template)
        file_path.write_text(content, encoding="utf-8")

        self._logger.info(f"Saved template to {file_path}")
        return {
            "saved": True,
            "file_path": str(file_path),
            "content": content,
        }

    def _assemble_template(self, template: dict[str, Any]) -> str:
        """将模板各部分组装为完整 .tex 源码。"""
        lines = []
        lines.append(template.get("preamble", r"\documentclass{article}"))
        lines.append("")
        lines.append(r"\begin{document}")
        lines.append("")

        sections = template.get("body_structure", [])
        for sec in sections:
            title = sec.get("section", "Untitled")
            hint = sec.get("suggested_content", "")
            lines.append(r"\section{" + title + "}")
            if hint:
                lines.append(f"% {hint}")
            lines.append("")

        lines.append(r"\end{document}")
        return "\n".join(lines)
