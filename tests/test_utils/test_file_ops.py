"""file_ops 单元测试。"""

from pathlib import Path
from unittest.mock import patch

from backend.utils.file_ops import atomic_write


def test_atomic_write_fsyncs_file(tmp_path):
    """atomic_write 写入后应对临时文件执行 fsync 再原子替换。"""
    target = tmp_path / "main.tex"

    with patch("backend.utils.file_ops.os.fsync") as fsync_mock:
        atomic_write(target, "hello")

    assert target.read_text(encoding="utf-8") == "hello"
    fsync_mock.assert_called_once()


def test_atomic_write_cleans_temp_on_failure(tmp_path):
    """写入失败时不应残留临时文件。"""
    target = tmp_path / "main.tex"

    with patch("backend.utils.file_ops.os.fdopen", side_effect=OSError("write failed")):
        try:
            atomic_write(target, "hello")
        except OSError:
            pass

    leftovers = [p for p in tmp_path.iterdir() if p != target]
    assert leftovers == []
