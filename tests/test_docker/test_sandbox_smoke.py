"""Docker 沙箱真容器冒烟（marker: docker，无 daemon / 缺镜像自动 skip）。

验收口径对应 v2 方案 §5.6：
- 有 Docker：最小 LaTeX 编译在容器内成功；
- 安全基线：只读根文件系统阻断越界写入、无网络阻断外联；
- 无 Docker 环境：整文件 skip，不影响回落回归。

运行方式：pytest -m docker（或全量运行，未满足条件自动跳过）。
"""

import shutil
import tempfile
from pathlib import Path

import pytest

from backend.core.config import get_config
from backend.utils.sandbox import ExecSpec, get_sandbox_runner

pytestmark = pytest.mark.docker


def _daemon_available() -> bool:
    import asyncio

    try:
        return asyncio.run(get_sandbox_runner().probe_docker())
    except Exception:  # noqa: BLE001
        return False


def _image_exists(image: str) -> bool:
    try:
        import docker as docker_sdk

        client = docker_sdk.from_env(timeout=10)
        client.images.get(image)
        return True
    except Exception:  # noqa: BLE001
        return False


@pytest.fixture(scope="module", autouse=True)
def _ensure_config_loaded():
    """模块级 fixture 先于函数级 conftest 加载，需自行保证配置就绪。"""
    from backend.core.config import load_config

    try:
        get_config()
    except RuntimeError:
        load_config("config.json")


def _host_tmp_visible_to_daemon() -> bool:
    """预检：本进程创建的临时目录对 daemon 是否可见。

    受限执行环境（文件沙箱的私有 /tmp、容器内测试进程）中，bind mount
    的 source path 在 daemon 视角不存在，容器创建必然 400——这属环境
    限制而非产品缺陷，显式 skip 以免掩盖真实回归。真实开发机 / CI 上
    此预检恒通过。
    """

    work = Path(tempfile.mkdtemp(prefix="lx_sbx_preflight_"))
    try:
        spec = ExecSpec(
            command=["true"],
            cwd=str(work),
            image_kind="tex",
            timeout_seconds=30,
        )
        try:
            res = _run_docker(spec)
        except RuntimeError as e:
            if "does not exist" in str(e) or "invalid mount" in str(e):
                return False
            raise
        return res.returncode == 0
    finally:
        shutil.rmtree(work, ignore_errors=True)


@pytest.fixture(scope="module")
def docker_available():
    if not _daemon_available():
        pytest.skip("Docker daemon 不可达，跳过沙箱容器冒烟")
    if not _host_tmp_visible_to_daemon():
        pytest.skip(
            "执行环境的临时目录对 Docker daemon 不可见（受限文件沙箱），"
            "无法验证 bind mount；请在宿主会话或 CI 中运行"
        )
    return True


def _run_docker(spec: ExecSpec):
    """强制 docker 后端执行（保存/恢复 sandbox.mode）。"""
    cfg = get_config()
    original = cfg.sandbox.mode
    cfg.sandbox.mode = "docker"
    try:
        import asyncio

        # 探测缓存可能为 False，强制刷新一次
        runner = get_sandbox_runner()
        runner._probe_ok = None  # noqa: SLF001 — 测试内重置探测缓存
        return asyncio.run(runner.execute(spec))
    finally:
        cfg.sandbox.mode = original


MINIMAL_TEX = r"""\documentclass{article}
\begin{document}
Hello, sandbox!
\end{document}
"""


class TestDockerSmoke:
    def test_image_configured(self, docker_available):
        cfg = get_config()
        assert cfg.sandbox.image_tex

    def test_out_of_bounds_write_blocked(self, docker_available):
        """只读根文件系统：越界路径写入必须失败，且不留宿主痕迹。"""
        cfg = get_config()
        if not _image_exists(cfg.sandbox.image_tex):
            pytest.skip(f"镜像不存在: {cfg.sandbox.image_tex}")
        work = Path(tempfile.mkdtemp(prefix="lx_sbx_sec_"))
        try:
            spec = ExecSpec(
                command=["touch", "/usr/share/blocked-by-sandbox"],
                cwd=str(work),
                image_kind="tex",
                timeout_seconds=30,
            )
            res = _run_docker(spec)
            assert res.returncode != 0
            assert not Path("/usr/share/blocked-by-sandbox").exists()
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def test_network_disabled(self, docker_available):
        """--network none：任何外联尝试必须立即失败。"""
        cfg = get_config()
        if not _image_exists(cfg.sandbox.image_tex):
            pytest.skip(f"镜像不存在: {cfg.sandbox.image_tex}")
        work = Path(tempfile.mkdtemp(prefix="lx_sbx_net_"))
        try:
            spec = ExecSpec(
                command=[
                    "git",
                    "ls-remote",
                    "https://example.com/repo.git",
                ],
                cwd=str(work),
                image_kind="tex",
                env={"LC_ALL": "C", "LANG": "C"},
                timeout_seconds=30,
            )
            res = _run_docker(spec)
            assert res.returncode != 0
            combined = (res.stderr + res.stdout).lower()
            assert any(
                token in combined
                for token in (
                    "could not resolve host",
                    "temporary failure in name resolution",
                    "network is unreachable",
                    "failed to connect",
                    "name or service not known",
                )
            ), f"预期网络被禁用的报错，实际输出：{combined[:300]}"
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def test_minimal_latex_compile(self, docker_available):
        """极小 LaTeX 样例在 tex 镜像内编译出 PDF。"""
        cfg = get_config()
        if not _image_exists(cfg.sandbox.image_tex):
            pytest.skip(f"镜像不存在: {cfg.sandbox.image_tex}")

        from backend.utils.tex_compile import run_tex_compile

        work = Path(tempfile.mkdtemp(prefix="lx_sbx_tex_"))
        try:
            (work / "main.tex").write_text(MINIMAL_TEX, encoding="utf-8")
            original = cfg.sandbox.mode
            cfg.sandbox.mode = "docker"
            try:
                import asyncio

                runner = get_sandbox_runner()
                runner._probe_ok = None  # noqa: SLF001
                result = asyncio.run(run_tex_compile("main.tex", work, 120))
            finally:
                cfg.sandbox.mode = original
            assert result.success, f"容器内编译失败：{result.raw_output[:500]}"
            assert (work / "main.pdf").exists()
        finally:
            shutil.rmtree(work, ignore_errors=True)


class TestConvertSmoke:
    """convert 镜像全链路冒烟：pandoc 文本转换在容器内成功。

    重点回归两个历史缺陷（P1a 评审发现）：
    - 镜像不得设置 ENTRYPOINT（否则 runner 的完整 argv 被追加为 ENTRYPOINT
      参数，实际执行 `pandoc pandoc ...` 必然失败）；
    - 输入走临时文件而非 stdin（docker 后端不注入标准输入）。
    """

    def test_pandoc_binary_with_full_argv(self, docker_available):
        """convert 镜像内以完整 argv 执行 pandoc --version（ENTRYPOINT 回归哨兵）。"""

        cfg = get_config()
        if not _image_exists(cfg.sandbox.image_convert):
            pytest.skip(f"镜像不存在: {cfg.sandbox.image_convert}")

        work = Path(tempfile.mkdtemp(prefix="lx_sbx_conv_bin_"))
        try:
            spec = ExecSpec(
                command=["pandoc", "--version"],
                cwd=str(work),
                image_kind="convert",
                timeout_seconds=30,
            )
            res = _run_docker(spec)
            assert res.returncode == 0, f"pandoc --version 失败：{res.stderr[:300]}"
            assert "pandoc" in res.stdout.lower()
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def test_markdown_to_html(self, docker_available):
        """markdown → html 文本转换在容器内全链路成功（临时文件输入路径）。"""
        import asyncio

        cfg = get_config()
        if not _image_exists(cfg.sandbox.image_convert):
            pytest.skip(f"镜像不存在: {cfg.sandbox.image_convert}")

        from backend.modules.format_converter.service import FormatConverterService

        original = cfg.sandbox.mode
        cfg.sandbox.mode = "docker"
        try:
            runner = get_sandbox_runner()
            runner._probe_ok = None  # noqa: SLF001 — 重置探测缓存强制走 docker
            service = FormatConverterService()
            result = asyncio.run(
                service._run_pandoc_text("# Hello", "markdown", "html")
            )
        finally:
            cfg.sandbox.mode = original
        text = str(result)
        assert "Hello" in text and "<h1" in text, f"转换结果异常：{text[:200]}"
