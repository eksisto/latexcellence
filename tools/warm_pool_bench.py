#!/usr/bin/env python3
"""warm pool 冷启动基准（部署收口阶段三，维护者工具，自包含可独立运行）。

同一 ExecSpec 先走冷启路径 N 次（warm_pool_enabled=false，逐次即时
create），再走池路径 N 次（warm_pool_enabled=true，命中预创建容器直接
start），分别输出准备耗时（容器 create+start，即 execute 发起到容器
就绪）与整次 execute 墙钟的 P50/P95。

用法（仓库根目录）：
    .venv/bin/python tools/warm_pool_bench.py                 # tex 镜像，N=20
    .venv/bin/python tools/warm_pool_bench.py --n 30 --image-kind convert

验收标准：池路径准备耗时 P50 显著低于冷启基线（既有 docker 冒烟保持
绿色为前置）。池路径 P50 不降反升时脚本以退出码 1 结束。

说明：准备耗时取自 docker 后端 execute 记录的 `_last_prep`（与日志行
"sandbox container ready | prep=..." 同源），两次相位除 warm pool 开关
外全部条件一致（同 ExecSpec / 同镜像 / 同资源限额）。
"""

import argparse
import asyncio
import math
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.core.config import get_config, load_config  # noqa: E402
from backend.core.logger import setup_logging  # noqa: E402
from backend.utils.sandbox.docker_backend import DockerBackend  # noqa: E402
from backend.utils.sandbox.models import ExecSpec, SandboxLimits  # noqa: E402


def _pct(values: list[float], p: float) -> float:
    """线性插值百分位（毫秒）。"""
    s = sorted(values)
    k = (len(s) - 1) * p / 100
    f, c = math.floor(k), math.ceil(k)
    if f == c:
        return s[int(k)]
    return s[f] * (c - k) + s[c] * (k - f)


def _row(label: str, prep: list[float], wall: list[float]) -> None:
    print(
        f"  {label:<12} P50={_pct(prep, 50):7.1f}ms  P95={_pct(prep, 95):7.1f}ms"
        f"   | wall P50={_pct(wall, 50) / 1000:6.3f}s"
        f"  P95={_pct(wall, 95) / 1000:6.3f}s"
    )


async def _run_phase(
    backend: DockerBackend,
    spec: ExecSpec,
    image: str,
    limits: SandboxLimits,
    n: int,
) -> tuple[list[float], list[float]]:
    """执行 N 次并返回 (准备耗时列表, 墙钟列表)（毫秒）。"""
    preps: list[float] = []
    walls: list[float] = []
    for i in range(n):
        t0 = time.monotonic()
        res = await backend.execute(spec, image=image, limits=limits)
        walls.append((time.monotonic() - t0) * 1000)
        if res.returncode != 0:
            raise RuntimeError(
                f"bench command failed (rc={res.returncode}): {res.stderr[:200]}"
            )
        if backend._last_prep is None:
            raise RuntimeError("backend did not record prep timing")
        preps.append(backend._last_prep[0])
        print(
            f"    run {i + 1:>2}/{n}: prep={preps[-1]:7.1f}ms "
            f"wall={walls[-1] / 1000:6.3f}s pool={backend._last_prep[1]}"
        )
    return preps, walls


async def _bench(args: argparse.Namespace) -> int:
    cfg = get_config()
    cfg.sandbox.mode = "docker"  # 基准进程内强制 docker 路径
    image = {
        "tex": cfg.sandbox.image_tex,
        "convert": cfg.sandbox.image_convert,
        "plot": cfg.sandbox.image_plot,
    }[args.image_kind]
    limits = SandboxLimits(
        cpus=cfg.sandbox.cpus,
        memory_mb=cfg.sandbox.memory_mb,
        pids_limit=cfg.sandbox.pids_limit,
        tmpfs_mb=cfg.sandbox.tmpfs_mb,
    )

    backend = DockerBackend()
    if not await backend.ping():
        print("Docker daemon 不可达，无法运行基准", file=sys.stderr)
        return 2
    if not backend.preflight_mount_check(image):
        print("bind-mount 预检失败（临时目录对 daemon 不可见）", file=sys.stderr)
        return 2

    work = Path(tempfile.mkdtemp(prefix="lx_warm_pool_bench_"))
    spec = ExecSpec(
        command=["true"],
        cwd=str(work),
        image_kind=args.image_kind,
        timeout_seconds=args.timeout,
    )
    print(
        f"warm pool bench | image={image} | kind={args.image_kind} | N={args.n} "
        f"| depth={cfg.sandbox.max_concurrent_global}"
    )
    try:
        # ── 相位 A：冷启基线（开关关闭，逐次即时 create）─────────
        cfg.sandbox.warm_pool_enabled = False
        print(f"[phase A] cold baseline (warm_pool_enabled=false), N={args.n}")
        cold_prep, cold_wall = await _run_phase(backend, spec, image, limits, args.n)

        # ── 相位 B：池路径（开关开启；先预热一次并等补充落池）─────
        cfg.sandbox.warm_pool_enabled = True
        print(f"[phase B] warm pool (warm_pool_enabled=true), N={args.n}")
        await _run_phase(backend, spec, image, limits, 1)  # 预热跑（不计入）
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            if backend._pool.get(args.image_kind):
                break
            await asyncio.sleep(0.2)
        else:
            print("补充任务未落池，池路径基准中止", file=sys.stderr)
            return 2
        warm_prep, warm_wall = await _run_phase(backend, spec, image, limits, args.n)
        hits = backend._pool_stats["hit"]

        # ── 汇总 ─────────────────────────────────────────────────
        print("\n== 结果（准备耗时 = execute 发起到容器就绪）==")
        _row("cold", cold_prep, cold_wall)
        _row("warm pool", warm_prep, warm_wall)
        print(f"  pool hits: {hits}/{args.n + 1} executions in phase B " "(含预热跑)")
        gain = _pct(cold_prep, 50) - _pct(warm_prep, 50)
        ratio = _pct(warm_prep, 50) / _pct(cold_prep, 50)
        print(f"  prep P50: {gain:.1f}ms saved " f"(warm = {ratio:.0%} of cold)")
        return 0 if _pct(warm_prep, 50) < _pct(cold_prep, 50) else 1
    finally:
        # 清场：先等在途补充任务退出（延迟触发，避免清场后又落一个新容器），
        # 清空进程内池后按孤儿规则清退，再兜底直删
        try:
            wait_deadline = time.monotonic() + 30
            while time.monotonic() < wait_deadline:
                with backend._pool_guard:
                    if backend._refill_active == 0:
                        break
                time.sleep(0.1)
            with backend._pool_guard:
                backend._pool.clear()
                backend._inflight.clear()
            backend._reconcile_sync(grace_seconds=0)
            import docker as docker_sdk

            client = docker_sdk.from_env(timeout=10)
            for c in client.containers.list(
                all=True,
                filters={
                    "label": [
                        "latexcellence.warm_pool=1",
                        f"latexcellence.warm_pool.replica={backend._replica_id}",
                    ]
                },
            ):
                try:
                    c.remove(force=True)
                except Exception:  # noqa: BLE001
                    pass
        finally:
            backend.close()
            shutil.rmtree(work, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--n", type=int, default=20, help="每相位执行次数（默认 20）")
    parser.add_argument(
        "--image-kind",
        choices=("tex", "convert", "plot"),
        default="tex",
        help="镜像类别（默认 tex）",
    )
    parser.add_argument(
        "--timeout", type=float, default=60, help="单次执行超时秒数（默认 60）"
    )
    parser.add_argument(
        "--config", default="config.json", help="配置文件路径（默认 config.json）"
    )
    args = parser.parse_args()

    load_config(args.config)
    cfg = get_config()
    setup_logging(level="ERROR", log_dir=cfg.logging.dir)  # 压掉 INFO 日志噪音
    try:
        return asyncio.run(_bench(args))
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
