"""
project_router /browse 与 /open 的 HTTP 端点测试。

覆盖内容：
    - /browse 正常列出子目录
    - /browse 大小写不敏感解析（Linux 输入错误大小写仍能定位到真实目录）
    - /browse 多级路径中间组件的 casefold 匹配
    - /browse 不存在路径 / 目标是文件 / 空路径
    - /open 大小写不敏感解析（直接传入错误大小写路径）
"""

from pathlib import Path

import pytest


class TestBrowseDirectory:
    """/api/project/browse 端点测试。"""

    @pytest.fixture
    def client(self):
        from fastapi.testclient import TestClient

        from main import app

        return TestClient(app)

    def test_browse_lists_subdirectories(self, client, tmp_path):
        (tmp_path / "Alpha").mkdir()
        (tmp_path / "Beta").mkdir()
        (tmp_path / "ignored.txt").write_text("x")

        resp = client.get("/api/project/browse", params={"path": str(tmp_path)})

        assert resp.status_code == 200
        data = resp.json()
        names = {d["name"] for d in data["directories"]}
        assert names == {"Alpha", "Beta"}
        assert data["current"] == str(tmp_path.resolve())

    def test_browse_case_insensitive_leaf(self, client, tmp_path):
        real = tmp_path / "LaTeX"
        real.mkdir()
        wrong_case = tmp_path / "latex"  # 与真实目录仅大小写不同

        resp = client.get("/api/project/browse", params={"path": str(wrong_case)})

        assert resp.status_code == 200
        data = resp.json()
        # current 应纠正为磁盘上的真实大小写路径
        assert data["current"] == str(real.resolve())
        assert data["current"].endswith("LaTeX")

    def test_browse_case_insensitive_deep_component(self, client, tmp_path):
        real = tmp_path / "LaTeX" / "SubDir"
        real.mkdir(parents=True)
        wrong_case = tmp_path / "latex" / "subdir"

        resp = client.get("/api/project/browse", params={"path": str(wrong_case)})

        assert resp.status_code == 200
        data = resp.json()
        assert data["current"] == str(real.resolve())

    def test_browse_nonexistent_returns_400(self, client, tmp_path):
        resp = client.get(
            "/api/project/browse",
            params={"path": str(tmp_path / "no_such_dir")},
        )

        assert resp.status_code == 400
        assert "does not exist" in resp.json()["detail"]

    def test_browse_file_returns_400(self, client, tmp_path):
        f = tmp_path / "a.txt"
        f.write_text("x")

        resp = client.get("/api/project/browse", params={"path": str(f)})

        assert resp.status_code == 400
        assert "Not a directory" in resp.json()["detail"]

    def test_browse_empty_path_returns_cwd(self, client):
        resp = client.get("/api/project/browse", params={"path": ""})

        assert resp.status_code == 200
        assert resp.json()["current"] == str(Path.cwd().resolve())


class TestOpenProjectCaseInsensitive:
    """/api/project/open 对错误大小写路径的解析。"""

    @pytest.fixture
    def client(self):
        from fastapi.testclient import TestClient

        from main import app

        return TestClient(app)

    def test_open_case_insensitive(self, client, tmp_path):
        real = tmp_path / "MyProject"
        real.mkdir()
        (real / "main.tex").write_text(
            r"\documentclass{article}\begin{document}hi\end{document}"
        )
        wrong_case = tmp_path / "myproject"

        resp = client.post(
            "/api/project/open",
            params={"workspace_id": "test-ws-ci"},
            json={"path": str(wrong_case)},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["root_path"] == str(real.resolve())

    def test_open_nonexistent_returns_400(self, client, tmp_path):
        resp = client.post(
            "/api/project/open",
            params={"workspace_id": "test-ws-missing"},
            json={"path": str(tmp_path / "definitely_missing")},
        )

        assert resp.status_code == 400
