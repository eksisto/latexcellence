"""CI 专用：从 config.example.json 生成评测用 config.json（api_key 由环境变量注入）。

backend/core/config.py 的 SECRET_ENV_PATHS 会在加载配置时把环境变量
LX_LLM_KEY 注入 llm.api_key（优先于配置文件且不落盘），因此本脚本只负责
复制模板并应用可选的 base_url / model 覆盖：

    - LATEXCELLENCE_CI_LLM_BASE_URL   覆盖 llm.base_url（可选）
    - LATEXCELLENCE_CI_LLM_MODEL      覆盖 llm.model（可选）

安全：仓库根目录已存在 config.json 时拒绝执行（本地开发者的真实配置
不被覆盖），CI 环境没有 config.json 所以总能生成；需要覆盖时传 --force。

用法：
    python tools/ci/build_config.py [--force]
"""

import argparse
import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
EXAMPLE = PROJECT_ROOT / "config.example.json"
TARGET = PROJECT_ROOT / "config.json"

ENV_OVERRIDES = {
    "LATEXCELLENCE_CI_LLM_BASE_URL": ("llm", "base_url"),
    "LATEXCELLENCE_CI_LLM_MODEL": ("llm", "model"),
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--force",
        action="store_true",
        help="覆盖已存在的 config.json（默认拒绝，保护本地真实配置）",
    )
    args = parser.parse_args()

    if TARGET.exists() and not args.force:
        print(
            f"错误：{TARGET} 已存在，拒绝覆盖（本地真实配置）。"
            "确认要重建评测配置时使用 --force。",
            file=sys.stderr,
        )
        return 1

    cfg = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    applied = []
    for env, (section, key) in ENV_OVERRIDES.items():
        value = os.environ.get(env)
        if value:
            cfg.setdefault(section, {})[key] = value
            applied.append(f"{section}.{key} <- {env}")
    TARGET.write_text(
        json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"已生成评测配置: {TARGET}（api_key 由环境变量 LX_LLM_KEY 注入，不落盘）")
    for line in applied:
        print(f"  覆盖: {line}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
