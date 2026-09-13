"""convert 镜像 rar 解压沙箱真容器冒烟（marker: docker，三重 skip 门）。

验收口径对应 deploy-followups.md §4.1 阶段一：
- 有 Docker 且 convert 镜像存在：store 模式 RAR4 样本经 extract_archive
  全链路（预扫 → 沙箱解压 → 归一化/复核）在真容器内解压成功；
- 加密 / 分卷包走既有确定性拒绝路径（错误码不变）；
- 压缩包路径以只读方式挂载：容器内写挂载源必须失败（安全基线断言）；
- 无 daemon / 缺镜像 / 临时目录对 daemon 不可见：整文件 skip，不影响回落回归。

fixtures 复用 tests/fixtures/rar/ 入库样本（store 模式 RAR4；真实压缩
RAR4/RAR5 样本已由部署收口阶段二入库，如需真容器路径覆盖可在本文件增补，
local 模式双工具端到端见 tests/test_multiuser/test_upload_rar.py）。
运行方式：pytest -m docker（或全量运行，未满足条件自动跳过）。
"""

import shutil
import tempfile
from pathlib import Path

import pytest

from backend.core.config import get_config
from backend.core.multiuser import upload
from backend.utils.sandbox import ExecSpec, SandboxMount, get_sandbox_runner
from tests.test_docker.test_sandbox_smoke import (
    _daemon_available,
    _host_tmp_visible_to_daemon,
    _image_exists,
    _run_docker,
)

pytestmark = pytest.mark.docker

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "rar"


@pytest.fixture(scope="module", autouse=True)
def _ensure_config_loaded():
    """模块级 fixture 先于函数级 conftest 加载，需自行保证配置就绪。"""
    try:
        get_config()
    except RuntimeError:
        from tests.conftest import load_repo_config

        load_repo_config()


@pytest.fixture(scope="module")
def docker_available():
    if not _daemon_available():
        pytest.skip("Docker daemon 不可达，跳过 rar 沙箱容器冒烟")
    if not _host_tmp_visible_to_daemon():
        pytest.skip(
            "执行环境的临时目录对 Docker daemon 不可见（受限文件沙箱），"
            "无法验证 bind mount；请在宿主会话或 CI 中运行"
        )
    return True


def _require_convert_image() -> str:
    cfg = get_config()
    if not _image_exists(cfg.sandbox.image_convert):
        pytest.skip(f"镜像不存在: {cfg.sandbox.image_convert}")
    return cfg.sandbox.image_convert


def _limits() -> upload.UploadLimits:
    return upload.UploadLimits(
        max_archive_bytes=8 * 1024 * 1024,
        max_files=100,
        max_file_size_bytes=1024 * 1024,
        max_compression_ratio=1000,
        remaining_quota_bytes=10 * 1024 * 1024,
        timeout_seconds=120,
    )


def _staging(tmp_path: Path) -> tuple[Path, Path]:
    staging = Path(tempfile.mkdtemp(prefix="lx_rar_sbx_", dir=str(tmp_path)))
    extract_dir = staging / "extracted"
    extract_dir.mkdir(parents=True, exist_ok=False)
    return staging, extract_dir


class TestRarSandboxSmoke:
    def test_extractor_binaries_in_image(self, docker_available):
        """convert 镜像内置 unar 与 bsdtar（构建期 command -v 自检的运行期对账）。"""
        _require_convert_image()
        for binary, args in (("unar", ["-v"]), ("bsdtar", ["--version"])):
            work = Path(tempfile.mkdtemp(prefix="lx_rar_sbx_bin_"))
            try:
                spec = ExecSpec(
                    command=[binary, *args],
                    cwd=str(work),
                    image_kind="convert",
                    timeout_seconds=30,
                )
                res = _run_docker(spec)
                assert (
                    res.returncode == 0
                ), f"{binary} 在 convert 镜像内不可用：{res.stderr[:200]}"
            finally:
                shutil.rmtree(work, ignore_errors=True)

    def test_normal_store_rar_extracts_in_container(self, docker_available, tmp_path):
        """正常包解压全链路：预扫放行 → 容器内 unar 解压 → 宿主复核入库形态。"""
        _require_convert_image()
        staging, extract_dir = _staging(tmp_path)
        try:
            original = get_config().sandbox.mode
            get_config().sandbox.mode = "docker"
            runner = get_sandbox_runner()
            runner._probe_ok = None  # noqa: SLF001 — 重置探测缓存强制走 docker
            try:
                res = upload.extract_archive(
                    FIXTURES / "layout_top_dir.rar", extract_dir, _limits()
                )
            finally:
                get_config().sandbox.mode = original
            # 顶层单目录布局归一化为项目根，内容与入库 fixture 一致
            assert res.root == extract_dir / "proj"
            assert (res.root / "main.tex").read_text(
                encoding="utf-8"
            ) == "\\documentclass{article}\nWorld\n"
            assert res.file_count == 2
        finally:
            shutil.rmtree(staging, ignore_errors=True)

    def test_bsdtar_fallback_extracts_in_container(self, docker_available, tmp_path):
        """bsdtar 分支：注册表命令构造在真容器内可执行（回退路径运行期对账）。"""
        _require_convert_image()
        from backend.core.multiuser import upload_rar

        staging, extract_dir = _staging(tmp_path)
        archive = staging / "upload.rar"
        shutil.copyfile(FIXTURES / "layout_flat.rar", archive)
        cmd = upload_rar.RAR_EXTRACTOR_CMD_BUILDERS["bsdtar"](
            "bsdtar", archive, extract_dir
        )
        try:
            spec = ExecSpec(
                command=cmd,
                cwd=str(extract_dir),
                image_kind="convert",
                mounts=[SandboxMount(host_path=str(archive), read_only=True)],
                timeout_seconds=60,
            )
            res = _run_docker(spec)
            assert res.returncode == 0, f"bsdtar 解压失败：{res.stderr[:300]}"
            assert (extract_dir / "main.tex").read_text(
                encoding="utf-8"
            ) == "\\documentclass{article}\nHello\n"
        finally:
            shutil.rmtree(staging, ignore_errors=True)

    def test_protected_rar_rejected(self, docker_available, tmp_path):
        """加密 / 分卷包拒绝路径：预扫确定性拦截，错误码不变。"""
        _require_convert_image()
        staging, extract_dir = _staging(tmp_path)
        cfg = get_config()
        original = cfg.sandbox.mode
        try:
            cfg.sandbox.mode = "docker"
            runner = get_sandbox_runner()
            runner._probe_ok = None  # noqa: SLF001
            for name in ("encrypted.rar", "volume.part1.rar"):
                with pytest.raises(upload.ArchiveError) as ei:
                    upload.extract_archive(FIXTURES / name, extract_dir, _limits())
                assert ei.value.code == "archive_unsafe"
        finally:
            cfg.sandbox.mode = original
            shutil.rmtree(staging, ignore_errors=True)

    def test_archive_mount_is_read_only(self, docker_available, tmp_path):
        """压缩包路径只读挂载：容器内写挂载源必须失败，且包字节不被篡改。"""
        _require_convert_image()
        staging, extract_dir = _staging(tmp_path)
        archive = staging / "upload.rar"
        shutil.copyfile(FIXTURES / "layout_flat.rar", archive)
        original = archive.read_bytes()
        try:
            spec = ExecSpec(
                command=["touch", str(archive)],
                cwd=str(extract_dir),
                image_kind="convert",
                mounts=[SandboxMount(host_path=str(archive), read_only=True)],
                timeout_seconds=30,
            )
            res = _run_docker(spec)
            assert res.returncode != 0, "容器内写只读挂载源竟然成功"
            assert archive.read_bytes() == original
        finally:
            shutil.rmtree(staging, ignore_errors=True)
