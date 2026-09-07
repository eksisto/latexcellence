"""多用户模式 P3：上传解压管线安全测试。

覆盖 zip / 7z 两种格式的：
    - 正常解压与项目根目录归一化（顶层目录布局 / 直接内容布局）
    - 路径穿越（../、绝对路径）拒绝
    - 符号链接入库拒绝
    - 加密压缩包拒绝
    - 大小写不敏感重名拒绝
    - 压缩炸弹（过高压缩比）拒绝
    - 文件数 / 单文件大小 / 解压总量超限拒绝
    - GBK 文件名解码
    - 7z 解压

安全用例直接驱动 backend.core.multiuser.upload 的核心函数（构造自定义
UploadLimits 模拟配额边界），不依赖真实网络。
"""

import io
import shutil
import zipfile
from pathlib import Path

import py7zr
import pytest

from backend.core.multiuser import upload


def _zip_bytes(items: dict[str, bytes]) -> bytes:
    """构造 zip 内存字节（name -> content），使用 deflate 以体现真实压缩比。"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for name, content in items.items():
            z.writestr(name, content)
    return buf.getvalue()


def _limits(**overrides) -> upload.UploadLimits:
    base = dict(
        max_archive_bytes=1024 * 1024,
        max_files=100,
        max_file_size_bytes=1024 * 1024,
        max_compression_ratio=1000,
        remaining_quota_bytes=10 * 1024 * 1024,
        timeout_seconds=60,
    )
    base.update(overrides)
    return upload.UploadLimits(**base)


@pytest.fixture
def extract_dir(tmp_path) -> Path:
    d = tmp_path / "extracted"
    d.mkdir(parents=True, exist_ok=True)
    return d


@pytest.fixture
def archive_file(tmp_path) -> Path:
    p = tmp_path / "upload"
    p.mkdir(parents=True, exist_ok=True)
    return p / "a.zip"


# ── 正常解压 ─────────────────────────────────────────────────────
class TestNormalExtract:
    def test_zip_with_top_level_dir(self, extract_dir, archive_file):
        archive_file.write_bytes(
            _zip_bytes({"proj/main.tex": "main", "proj/a.tex": "a"})
        )
        res = upload.extract_archive(archive_file, extract_dir, _limits())
        # 顶层仅一个目录 → 归一化为项目根
        assert res.root == extract_dir / "proj"
        assert (res.root / "main.tex").read_text(encoding="utf-8") == "main"
        assert res.size > 0

    def test_zip_direct_content_layout(self, extract_dir, archive_file):
        archive_file.write_bytes(_zip_bytes({"main.tex": "main", "a.tex": "a"}))
        res = upload.extract_archive(archive_file, extract_dir, _limits())
        assert res.root == extract_dir
        assert (res.root / "main.tex").exists()

    def test_zip_nested_with_multiple_top_dirs_uses_extract_root(
        self, extract_dir, archive_file
    ):
        archive_file.write_bytes(_zip_bytes({"a/main.tex": "1", "b/other.tex": "2"}))
        res = upload.extract_archive(archive_file, extract_dir, _limits())
        assert res.root == extract_dir

    def test_7z_extract(self, extract_dir, tmp_path):
        src = tmp_path / "src"
        (src / "pkg").mkdir(parents=True)
        (src / "main.tex").write_text("hello", encoding="utf-8")
        (src / "pkg" / "x.tex").write_text("x", encoding="utf-8")
        seven = tmp_path / "a.7z"
        with py7zr.SevenZipFile(str(seven), "w") as z:
            z.write(str(src / "main.tex"), "proj/main.tex")
            z.write(str(src / "pkg" / "x.tex"), "proj/pkg/x.tex")
        res = upload.extract_archive(seven, extract_dir, _limits())
        assert res.root == extract_dir / "proj"
        assert (res.root / "main.tex").read_text(encoding="utf-8") == "hello"


# ── 安全拦截 ─────────────────────────────────────────────────────
class TestSecurity:
    def test_rejects_path_traversal(self, extract_dir, archive_file):
        archive_file.write_bytes(_zip_bytes({"../../evil.txt": b"x"}))
        with pytest.raises(upload.ArchiveError) as ei:
            upload.extract_archive(archive_file, extract_dir, _limits())
        assert ei.value.code == "archive_unsafe"

    def test_rejects_absolute_path(self, extract_dir, archive_file):
        archive_file.write_bytes(_zip_bytes({"/tmp/evil.txt": b"x"}))
        with pytest.raises(upload.ArchiveError) as ei:
            upload.extract_archive(archive_file, extract_dir, _limits())
        assert ei.value.code == "archive_unsafe"

    def test_rejects_unsupported_suffix(self, extract_dir, tmp_path):
        p = tmp_path / "a.tar"
        p.write_bytes(b"x")
        with pytest.raises(upload.ArchiveError) as ei:
            upload.extract_archive(p, extract_dir, _limits())
        assert ei.value.code == "invalid_archive"

    def test_rejects_case_insensitive_duplicate(self, extract_dir, archive_file):
        archive_file.write_bytes(_zip_bytes({"README.tex": b"1", "readme.tex": b"2"}))
        with pytest.raises(upload.ArchiveError) as ei:
            upload.extract_archive(archive_file, extract_dir, _limits())
        assert ei.value.code == "archive_unsafe"

    def test_rejects_symlink_entry(self, extract_dir, archive_file):
        # 手工构造符号链接条目（外部属性高四位 0o120777）
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            zi = zipfile.ZipInfo("link.txt")
            zi.create_system = 3
            zi.external_attr = (0o120777 & 0xFFFF) << 16
            z.writestr(zi, b"")  # symlink 目标内容
        archive_file.write_bytes(buf.getvalue())
        with pytest.raises(upload.ArchiveError) as ei:
            upload.extract_archive(archive_file, extract_dir, _limits())
        assert ei.value.code == "archive_unsafe"

    @pytest.mark.skipif(shutil.which("7z") is None, reason="7z 工具不可用")
    def test_rejects_encrypted_zip(self, extract_dir, tmp_path, archive_file):
        # 用 7z 生成带口令的 zip（真实加密：flag 0x1）
        src = tmp_path / "enc"
        src.mkdir()
        (src / "secret.txt").write_text("data", encoding="utf-8")
        enc = tmp_path / "enc.zip"
        import subprocess

        subprocess.run(
            ["7z", "a", "-tzip", "-psecret", str(enc), "secret.txt"],
            cwd=str(src),
            check=True,
            capture_output=True,
        )
        archive_file.write_bytes(enc.read_bytes())
        with pytest.raises(upload.ArchiveError) as ei:
            upload.extract_archive(archive_file, extract_dir, _limits())
        assert ei.value.code == "archive_unsafe"

    def test_rejects_compression_bomb(self, extract_dir, archive_file):
        # 压缩比 1:1+ 即触发（max_compression_ratio=1）
        archive_file.write_bytes(_zip_bytes({"big.tex": b"a" * 1000}))
        with pytest.raises(upload.ArchiveError) as ei:
            upload.extract_archive(
                archive_file, extract_dir, _limits(max_compression_ratio=1)
            )
        assert ei.value.code == "archive_unsafe"

    def test_rejects_encrypted_7z(self, extract_dir, tmp_path):
        seven = tmp_path / "e.7z"
        with py7zr.SevenZipFile(str(seven), "w", password="secret") as z:
            z.writestr(b"data", "a.txt")
        with pytest.raises(upload.ArchiveError) as ei:
            upload.extract_archive(seven, extract_dir, _limits())
        assert ei.value.code == "archive_unsafe"


# ── 限额 ─────────────────────────────────────────────────────────
class TestQuotaLimits:
    def test_file_count_limit(self, extract_dir, archive_file):
        archive_file.write_bytes(
            _zip_bytes({"a.tex": b"1", "b.tex": b"2", "c.tex": b"3"})
        )
        with pytest.raises(upload.ArchiveError) as ei:
            upload.extract_archive(archive_file, extract_dir, _limits(max_files=2))
        assert ei.value.code == "upload_too_large"

    def test_single_file_size_limit(self, extract_dir, archive_file):
        archive_file.write_bytes(_zip_bytes({"big.tex": b"a" * 100}))
        with pytest.raises(upload.ArchiveError) as ei:
            upload.extract_archive(
                archive_file, extract_dir, _limits(max_file_size_bytes=50)
            )
        assert ei.value.code == "upload_too_large"

    def test_total_size_quota_limit(self, extract_dir, archive_file):
        archive_file.write_bytes(_zip_bytes({"a.tex": b"a" * 200, "b.tex": b"b" * 200}))
        with pytest.raises(upload.ArchiveError) as ei:
            upload.extract_archive(
                archive_file, extract_dir, _limits(remaining_quota_bytes=300)
            )
        assert ei.value.code == "quota_exceeded"

    def test_gbk_filename_decoded(self):
        """GBK 文件名：非 UTF-8 标志下 cp437 回退再按 GBK 解码。"""
        zi = zipfile.ZipInfo.__new__(zipfile.ZipInfo)
        raw_gbk = "中文.txt".encode("gbk")
        cp437_name = raw_gbk.decode("cp437")
        zi.flag_bits = 0
        zi.filename = cp437_name
        zi.orig_filename = cp437_name
        assert upload._decode_zip_name(zi) == "中文.txt"


# ── 落位 ─────────────────────────────────────────────────────────
class TestMove:
    def test_move_into_user_space(self, tmp_path):
        from backend.core.multiuser import userspace
        from tests.test_multiuser.conftest import build_mu_env

        build_mu_env(tmp_path)
        staging = userspace.get_staging_root() / "upload1" / "extracted"
        staging.mkdir(parents=True)
        project_root = staging / "proj"
        (project_root / "main.tex").parent.mkdir(parents=True)
        (project_root / "main.tex").write_text("x", encoding="utf-8")
        upload.move_into_user_space(
            staging, project_root, user_id="u1", project_id="p1"
        )
        dest = userspace.project_dir("u1", "p1")
        assert (dest / "main.tex").read_text(encoding="utf-8") == "x"
        from backend.core.multiuser.db import close_db

        close_db()
        from tests.test_multiuser.conftest import _reset_config_state

        _reset_config_state()


class TestStagingRetention:
    """clean_staging 只清超期目录，不误删进行中上传（P3 安全修复）。"""

    def test_keeps_fresh_and_removes_stale(self, tmp_path):
        import os
        import time

        from backend.core.multiuser import quota, userspace
        from backend.core.multiuser.db import close_db
        from tests.test_multiuser.conftest import (
            _reset_config_state,
            build_mu_env,
        )

        try:
            build_mu_env(tmp_path)
            staging = userspace.get_staging_root()
            fresh = staging / "fresh-upload"
            stale = staging / "stale-upload"
            for d in (fresh, stale):
                d.mkdir(parents=True)
                (d / "upload.zip").write_bytes(b"x")
            # 新鲜目录 = 进行中的上传；陈旧目录 mtime 回拨到保留期之外
            old = time.time() - quota.STAGING_RETENTION_SECONDS * 2
            os.utime(stale, (old, old))

            assert quota.clean_staging() == 1
            assert fresh.is_dir()
            assert not stale.exists()
        finally:
            close_db()
            _reset_config_state()


class TestExtractWatch:
    """7z 解压监视线程：实时写盘量超限即关闭句柄中断解压（P3 安全修复）。"""

    def test_aborts_when_over_quota(self, tmp_path):
        import threading
        import time

        fp_path = tmp_path / "a.7z"
        fp_path.write_bytes(b"fake-archive-bytes")
        fp = open(fp_path, "rb")

        # 预置已写盘量超过剩余配额的解压目录（模拟伪造头部绕过预检）
        (tmp_path / "extracted").mkdir()
        big = tmp_path / "extracted" / "bomb.bin"
        big.write_bytes(b"\0" * 1024)

        limits = upload.UploadLimits(
            max_archive_bytes=1024 * 1024,
            max_files=100,
            max_file_size_bytes=1024 * 1024,
            max_compression_ratio=1000,
            remaining_quota_bytes=512,
            timeout_seconds=999,
        )
        stop = threading.Event()
        breach: list[str] = []
        watcher = upload._start_extract_watch(
            fp, tmp_path / "extracted", limits, time.monotonic() + 999, stop, breach
        )
        watcher.join(timeout=5)
        assert not watcher.is_alive()
        assert breach == ["quota_exceeded"]
        assert fp.closed
        stop.set()

    def test_normal_completion_not_interrupted(
        self, extract_dir, archive_file, tmp_path
    ):
        """诚实压缩包在监视下正常解压完成，不受干扰。"""
        src = tmp_path / "src"
        src.mkdir(parents=True)
        (src / "main.tex").write_text("hello", encoding="utf-8")
        with py7zr.SevenZipFile(str(archive_file.with_suffix(".7z")), "w") as z:
            z.write(str(src / "main.tex"), "proj/main.tex")
        res = upload.extract_archive(
            archive_file.with_suffix(".7z"),
            extract_dir,
            _limits(remaining_quota_bytes=1 << 20),
        )
        assert res.file_count == 1
        assert (extract_dir / "proj" / "main.tex").read_text(
            encoding="utf-8"
        ) == "hello"
