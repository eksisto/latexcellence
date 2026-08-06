"""
宏包规则表离线编译验证（CI 集成）。

规则：
    - 本机具备 TeX（pdflatex）时自动运行语料的编译验证
    - 不具备 TeX 时跳过（运行时不依赖 TeX，验证仅限维护/CI 阶段）
    - 设置环境变量 LATEX_PACKAGE_VERIFY=0 可显式跳过

验证逻辑与维护者脚本 tools/latex_packages/verify_rules.py 共用，
此处只做轻量断言：所有非跳过片段加载推荐宏包后应无残留编译错误。
"""

import os
import shutil
from pathlib import Path

import pytest

TOOLS_DIR = Path(__file__).resolve().parent.parent.parent / "tools"


def _pdflatex_available() -> bool:
    if os.environ.get("LATEX_PACKAGE_VERIFY", "1") == "0":
        return False
    return shutil.which("pdflatex") is not None


pytestmark = pytest.mark.skipif(
    not _pdflatex_available(),
    reason="TeX 不可用或 LATEX_PACKAGE_VERIFY=0，跳过编译验证",
)


def test_corpus_compiles_with_recommended_packages():
    """语料片段加载规则表推荐的宏包后应编译通过（残留错误为空）。"""
    import sys

    sys.path.insert(0, str(TOOLS_DIR))
    from latex_packages.verify_rules import verify_all

    report = verify_all()
    assert report, "编译验证报告不应为空"

    failed = [entry for entry in report if entry["status"] == "fail"]
    assert not failed, "以下语料片段加载推荐宏包后仍有编译错误：\n" + "\n".join(
        f"  {entry['fixture']}: {entry.get('residual_errors', [])}" for entry in failed
    )

    compiled = [entry for entry in report if entry["status"] == "pass"]
    assert compiled, "至少应有一个片段参与编译验证"
