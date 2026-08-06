"""
宏包规则表覆盖率报告（维护者专用）。

对照"常用宏包期望清单"（common_packages.txt）检查规则表是否覆盖，
输出尚未覆盖的宏包，指导下一批规则补充。

用法：
    python tools/latex_packages/coverage_report.py
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
COMMON_LIST = Path(__file__).resolve().parent / "common_packages.txt"


def _load_common_packages() -> list[str]:
    lines = COMMON_LIST.read_text(encoding="utf-8").splitlines()
    return [
        line.strip().lower()
        for line in lines
        if line.strip() and not line.strip().startswith("#")
    ]


def _load_covered_packages() -> set[str]:
    import sys as _sys

    _sys.path.insert(0, str(PROJECT_ROOT))
    from backend.utils.latex_package_rules import (
        COLUMN_LETTER_PACKAGES,
        COMMAND_PACKAGES,
        ENV_PACKAGES,
        IMPLICIT_PACKAGES,
    )

    covered = (
        set(COMMAND_PACKAGES.values())
        | set(ENV_PACKAGES.values())
        | set(COLUMN_LETTER_PACKAGES.values())
        | set(IMPLICIT_PACKAGES)
    )
    return {pkg.lower() for pkg in covered}


def main() -> int:
    common = _load_common_packages()
    covered = _load_covered_packages()
    uncovered = [pkg for pkg in common if pkg not in covered]

    print("=" * 60)
    print("宏包规则表覆盖率报告")
    print("=" * 60)
    print(f"期望清单共 {len(common)} 个常用宏包")
    print(f"已覆盖 {len(common) - len(uncovered)} / 未覆盖 {len(uncovered)}")
    if uncovered:
        print("\n尚未覆盖的常用宏包：")
        for pkg in sorted(uncovered):
            print(f"  - {pkg}")
    print("=" * 60)
    return 1 if uncovered else 0


if __name__ == "__main__":
    sys.exit(main())
