"""主文档自动识别优先级回归。

docs/backend-core.md 声明的优先级：精确 main.tex > 文件名含 main >
任意 .tex。目录按字母序扫描（``-`` 排在 ``.`` 之前），单次遍历里
子串命中即返回会让 main-chapter1.tex 抢占排在其后的 main.tex，
必须两段式遍历：先全树精确匹配，未命中再回退子串匹配。
"""

from backend.core.project_manager import FileNode, LatexProject


def _node(name: str, path: str) -> FileNode:
    return FileNode(name=name, path=path, is_dir=False)


def test_exact_main_wins_over_main_prefixed_sibling(tmp_path):
    """main.tex 与 main-chapter1.tex 并存时必须选中 main.tex。"""
    pm = LatexProject()
    tree = [
        # 字母序下 main-chapter1.tex 排在 main.tex 之前
        _node("main-chapter1.tex", "main-chapter1.tex"),
        _node("main.tex", "main.tex"),
    ]
    assert pm._find_main_file(tmp_path, tree) == "main.tex"


def test_exact_main_found_in_subdirectory(tmp_path):
    """根目录无精确匹配时，子目录中的 main.tex 优先于根目录含 main 的文件。"""
    pm = LatexProject()
    tree = [
        _node("main-extra.tex", "main-extra.tex"),
        FileNode(
            name="chapters",
            path="chapters",
            is_dir=True,
            children=[_node("main.tex", "chapters/main.tex")],
        ),
    ]
    assert pm._find_main_file(tmp_path, tree) == "chapters/main.tex"


def test_main_prefixed_file_without_exact_match(tmp_path):
    """无精确 main.tex 时回退到文件名含 main 的文件。"""
    pm = LatexProject()
    tree = [
        _node("chapter1.tex", "chapter1.tex"),
        _node("main-chapter1.tex", "main-chapter1.tex"),
    ]
    assert pm._find_main_file(tmp_path, tree) == "main-chapter1.tex"


def test_case_insensitive_exact_match(tmp_path):
    """精确匹配不区分大小写（Main.tex 视同 main.tex；扩展名过滤本身大小写敏感）。"""
    pm = LatexProject()
    tree = [
        _node("main-backup.tex", "main-backup.tex"),
        _node("Main.tex", "Main.tex"),
    ]
    assert pm._find_main_file(tmp_path, tree) == "Main.tex"


def test_fallback_first_top_level_tex(tmp_path):
    """完全无 main 线索时回退顶层第一个 .tex 文件。"""
    pm = LatexProject()
    tree = [
        _node("notes.txt", "notes.txt"),
        _node("chapter1.tex", "chapter1.tex"),
        _node("chapter2.tex", "chapter2.tex"),
    ]
    assert pm._find_main_file(tmp_path, tree) == "chapter1.tex"


def test_no_tex_file_returns_none(tmp_path):
    pm = LatexProject()
    tree = [_node("notes.txt", "notes.txt")]
    assert pm._find_main_file(tmp_path, tree) is None
