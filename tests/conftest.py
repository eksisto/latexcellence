"""
pytest 共享 fixtures，避免在各测试文件中重复定义。
"""

import struct
import zlib
from pathlib import Path

import pytest

from backend.core.config import AppConfig, get_config, load_config
from backend.utils.latex_parser import LatexParser


def _repo_config_path() -> Path:
    """仓库根配置路径：config.json（含密钥，不入库）优先，干净检出
    （CI / 新 clone）回退到入库的 config.example.json。"""
    project_root = Path(__file__).resolve().parent.parent
    config_path = project_root / "config.json"
    if not config_path.exists():
        return project_root / "config.example.json"
    return config_path


def apply_test_config_pins(cfg: AppConfig) -> None:
    """在内存中钉住环境敏感基线，保证测试不随本机 config.json 取值漂移。"""
    # 访问令牌属于本地运行环境配置：中间件行为由 test_security_middleware 单独
    # 覆盖，其余 API 测试统一在内存中绕过令牌校验，避免依赖开发者本机 config.json。
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
    # 联网搜索 / 学术检索的全局功能开关同理钉为「开」：工具类用例按此基线
    # 编写，需要关闭行为的用例自行 monkeypatch（如 Writing Chat 灰度总闸
    # 用例）。干净检出加载 config.example.json（默认关）时若不钉住，工具
    # 不进入登记路径，resolve 型用例会在事件等待上无限挂起。
    cfg.features.function_calling.web_search.enabled = True
    cfg.features.function_calling.scholarly_research.enabled = True
    # 沙箱模式同理强制 local：装了 docker SDK 的开发机/CI 上，auto 模式会把
    # 单测中的命令执行劫持进真实容器（且沙箱化进程的临时目录对 daemon 不可
    # 见），破坏测试确定性。需要真容器的用例走 docker marker（tests/test_docker/
    # 自行强制 mode=docker 并自管跳过逻辑）。
    from backend.utils.sandbox import reset_sandbox_runner

    cfg.sandbox.mode = "local"
    reset_sandbox_runner()


def load_repo_config() -> AppConfig:
    """加载仓库根配置并应用测试基线。

    供测试体内需要显式（重）加载配置的用例（template_gen / plot_gen /
    多用户回归等）使用，与 _ensure_config 共享同一套回退与钉住逻辑，
    避免隐式依赖开发者本机 config.json 的存在与取值。
    """
    cfg = load_config(str(_repo_config_path()))
    apply_test_config_pins(cfg)
    return cfg


@pytest.fixture(autouse=True)
def _ensure_config():
    """确保每次测试前 config 已加载，并钉住环境敏感基线。"""
    try:
        cfg = get_config()
    except RuntimeError:
        load_repo_config()
        return
    apply_test_config_pins(cfg)


@pytest.fixture(autouse=True)
def _clear_llm_cache():
    """每个用例前清空 LLM 响应缓存。

    缓存以 (模型, 提示词, 参数) 为键跨用例存活，会把前一个用例的 mock
    响应泄露给同输入的后一个用例，必须逐用例隔离。
    """
    from backend.utils.llm_cache import clear_llm_cache

    clear_llm_cache()


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
