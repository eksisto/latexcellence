"""维护者评测工具共用的 TeX Live 路径发现（不进入生产运行时）。

评测脚本通常由非交互 shell 启动，不会读取 ~/.bashrc 等用户配置，因此
`shutil.which("latexmk")` 可能找不到用户已安装的 TeX Live。该模块扫描常见
安装位置并把 bin 目录加入 ``PATH``，供 plotgen_opt / templategen_opt /
stylemodify_opt 等离线工具统一复用。
"""

import os
from pathlib import Path

from backend.core.env import load_env_file


def ensure_tex_path() -> None:
    """把常见 TeX Live Linux 安装目录加入 PATH，避免因 shell PATH 缺失误判。"""
    # TEXLIVE_BIN 可经项目根 .env 注入（tools 专属变量，不经 load_config）；
    # 此处幂等加载，不与 backend.core.config 的收口冲突
    load_env_file()
    current = os.environ.get("PATH", "")
    candidates: list[str] = []

    env_dir = os.environ.get("TEXLIVE_BIN", "")
    if env_dir:
        candidates.append(env_dir)

    for root in ("/usr/local/texlive", "/opt/texlive"):
        root_path = Path(root)
        if not root_path.is_dir():
            continue
        for version_dir in sorted(root_path.iterdir(), reverse=True):
            bin_root = version_dir / "bin"
            if not bin_root.is_dir():
                continue
            for bindir in bin_root.iterdir():
                if (bindir / "latexmk").exists():
                    candidates.append(str(bindir))

    for candidate in candidates:
        if candidate not in current.split(os.pathsep):
            current = candidate + os.pathsep + current
    if current:
        os.environ["PATH"] = current
