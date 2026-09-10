"""CI 用 LLM 连通性探测：跑评测前先做一次最小 chat 调用，失败即非零退出。

作用：防止 LLM 服务不可用 / Key 失效 / 模型名错误时，各套评测"静默回退
为原文"产出一堆假 clean 结果污染指标（语料式评测在无 Key 时只打印警告
仍会 exit 0）。探测用 max_retries=1 快速失败。

用法：
    python tools/ci/llm_probe.py
"""

import asyncio
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.core.config import get_config, load_config  # noqa: E402
from backend.core.llm_client import LLMClient  # noqa: E402


async def probe() -> str:
    llm = LLMClient()
    reply = await llm.chat(
        [{"role": "user", "content": "连通性测试：只回复 OK 两个字母"}],
        max_tokens=16,
        max_retries=1,
    )
    return (reply or "").strip()


def main() -> int:
    try:
        get_config()
    except RuntimeError:
        config_path = PROJECT_ROOT / "config.json"
        if not config_path.exists():
            print(
                "错误：未找到 config.json，请先运行 python tools/ci/build_config.py",
                file=sys.stderr,
            )
            return 1
        load_config(str(config_path))

    try:
        reply = asyncio.run(probe())
    except Exception as e:
        print(f"错误：LLM 连通性探测失败：{type(e).__name__}: {e}", file=sys.stderr)
        print(
            "请检查 secrets.LX_LLM_KEY、llm.base_url / model 配置"
            "（见 docs/eval-regression-ci.md）。",
            file=sys.stderr,
        )
        return 1

    print(f"LLM 连通性正常（模型 {get_config().llm.model}），回复：{reply[:80]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
