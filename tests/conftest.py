"""
pytest 共享 fixtures，避免在各测试文件中重复定义。
"""

import struct
import zlib
from pathlib import Path

import pytest

from backend.core.config import get_config, load_config
from backend.utils.latex_parser import LatexParser


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
    # 访问令牌属于本地运行环境配置：中间件行为由 test_security_middleware 单独
    # 覆盖，其余 API 测试统一在内存中绕过令牌校验，避免依赖开发者本机 config.json。
    cfg = get_config()
    cfg.security.access_token = ""
    # 多用户开关同理属于本地运行环境配置：通用测试套件统一以单用户模式运行
    # （多用户专项测试在 tests/test_multiuser 自行构建隔离配置环境，不受影响），
    # 避免开发者本机 config.json 打开 multi_user.enabled 后，
    # 既有单用户 API 测试（未登录）全部被会话认证拒绝（401）。
    cfg.multi_user.enabled = False
    # API Key 同理：Service 层测试统一 Mock LLM 客户端，但多数服务会先检查
    # cfg.llm.api_key 是否为空；这里在内存中强制使用 dummy key，保证测试
    # 不依赖本机 config.json，也绝不会在单测中发起真实 LLM 请求。
    # 需要验证“无 Key”行为的测试可自行 monkeypatch 置空。
    cfg.llm.api_key = "test-key"


@pytest.fixture
def parser():
    return LatexParser()


@pytest.fixture
def minimal_png():
    """生成最小有效 PNG 图片（1x1 红色像素）。"""

    def chunk(ctype, data):
        c = ctype + data
        return (
            struct.pack(">I", len(data))
            + c
            + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    idat = zlib.compress(b"\x00\xff\x00\x00")
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", idat)
        + chunk(b"IEND", b"")
    )
