#!/usr/bin/env python3
"""同步 config.example.json 的新增键到 config.json，不覆盖已有值。

用法：
  python3 scripts/sync_config.py          # dry-run，只看会新增什么
  python3 scripts/sync_config.py --apply  # 真正写回 config.json（先自动备份）

原理：深度合并，服务器现有键值一律保留，只有 example 里新增的键会被补入。
安全方向固定：真实值(服务器)优先，example 只补缺。绝不可反向覆盖。
"""

import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXAMPLE = ROOT / "config.example.json"
CONFIG = ROOT / "config.json"


def deep_merge(base, override):
    if not isinstance(base, dict) or not isinstance(override, dict):
        return base
    result = dict(base)
    for k, v in override.items():
        if k in result:
            result[k] = deep_merge(result[k], v)
        else:
            result[k] = v
    return result


def collect_added(a, b, path=""):
    added = []
    for k, v in b.items():
        p = f"{path}.{k}" if path else k
        if isinstance(v, dict) and isinstance(a.get(k), dict):
            added.extend(collect_added(a[k], v, p))
        elif k not in a:
            added.append(p)
    return added


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--apply", action="store_true", help="真正写回 config.json（先备份）"
    )
    args = parser.parse_args()

    if not CONFIG.exists():
        sys.exit(f"缺少 {CONFIG}，请确认在应用目录下运行")
    if not EXAMPLE.exists():
        sys.exit(f"缺少 {EXAMPLE}，请先 pull 最新代码")

    example = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    current = json.loads(CONFIG.read_text(encoding="utf-8"))

    added = collect_added(current, example)
    print(f"新增键 {len(added)} 个：")
    for p in added:
        print(f"  + {p}")

    if not added:
        print("config.json 已是最新，无需同步。")
        return 0

    if not args.apply:
        print("\n这是 dry-run，未改动任何文件。加 --apply 真正写入。")
        return 0

    backup = CONFIG.with_suffix(".json.bak")
    shutil.copy2(CONFIG, backup)
    print(f"\n已备份到 {backup}")

    merged = deep_merge(current, example)
    CONFIG.write_text(
        json.dumps(merged, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print("已写回 config.json。")
    print("提示：多数配置热重载；multi_user.enabled 变更需重启后端。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
