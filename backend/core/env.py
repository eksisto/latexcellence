""".env 环境变量文件加载（python-dotenv 统一入口）。

项目将凭证类配置（LLM API Key、SMTP 密码、Tavily / 学术数据源 Key、
首个管理员引导凭证等）经环境变量注入（见 config.SECRET_ENV_PATHS
注册表与多用户 admin_*_env 配置），部署时逐个 export 繁琐且易遗漏。
本模块统一加载项目根目录的 `.env` 文件，把文件内容注入
os.environ 后，后续所有 os.environ.get(...) 消费点自动生效。

调用时机：`backend.core.config._parse_config_file()` 开头统一调用
（`load_config()` 与热重载都经由该路径），入口点无需单独调用；
tools 专属变量（如 TEXLIVE_BIN）的消费点可自行调用（幂等）。

约定：
- 真实环境变量优先（override=False）：`.env` 只补缺，已在 shell /
  systemd / compose 中显式注入的值不会被文件覆盖；
- 幂等：默认路径只加载一次，重复调用不再读取；
- `.env` 含敏感信息，不入库（.gitignore 已忽略），模板见 `.env.example`。
"""

from pathlib import Path

from dotenv import load_dotenv

from backend.core.logger import get_logger

# backend/core/env.py → 项目根
_DEFAULT_ENV_PATH = Path(__file__).resolve().parents[2] / ".env"
_loaded_path: str | None = None


def load_env_file(path: str | Path | None = None) -> bool:
    """加载 .env 文件到 os.environ（默认路径幂等，真实环境变量优先）。

    参数：
        path: 显式指定 .env 文件路径（测试 / 特殊部署用）；
            缺省为项目根目录 `.env`

    返回：
        是否实际加载了文件（文件不存在或默认路径已加载过返回 False）
    """
    global _loaded_path
    if path is None and _loaded_path is not None:
        return False
    target = Path(path) if path is not None else _DEFAULT_ENV_PATH
    loaded = load_dotenv(target, override=False)
    if loaded:
        _loaded_path = str(target)
        # 只记文件名，绝不记录内容（可能含 API Key / 密码）
        get_logger(__name__).info("Loaded environment variables from %s", target.name)
    return bool(loaded)
