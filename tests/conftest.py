"""
pytest 共享 fixtures，避免在各测试文件中重复定义。
"""

import struct
import zlib
from pathlib import Path

import pytest

from backend.core.config import get_config, load_config
from backend.modules.typo_check.latex_parser import LatexParser


@pytest.fixture(autouse=True)
def _ensure_config():
    """确保每次测试前 config 已加载。"""
    try:
        get_config()
    except RuntimeError:
        project_root = Path(__file__).resolve().parent.parent
        config_path = project_root / "config.json"
        if config_path.exists():
            load_config(str(config_path))
        else:
            load_config("config.json")


@pytest.fixture
def parser():
    return LatexParser()


@pytest.fixture
def minimal_png():
    """生成最小有效 PNG 图片（1x1 红色像素）。"""

    def chunk(ctype, data):
        c = ctype + data
        return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    idat = zlib.compress(b"\x00\xff\x00\x00")
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")
