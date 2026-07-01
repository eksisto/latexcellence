"""
LaTeX 项目管理模块。

职责：
    1. 打开 LaTeX 项目目录，扫描 .tex/.bib/.cls 文件树
    2. 检测 \\include / \\input / \\subfile 依赖关系
    3. 按空行分割 .tex 文件为段落，记录每段在源文件中的行号范围
    4. 原地修改：用户审核确认后，精确写回对应段落

关键设计：
    - 段落分割基于空行（LaTeX 段落惯例）
    - 修改时检测原始缩进并保留
    - 写回前自动创建 .bak 备份文件
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from backend.core.logger import get_logger


@dataclass
class Paragraph:
    """
    段落数据结构。
    每个段落对应 .tex 文件中的一个 LaTeX 段落（以空行分隔）。
    index 和 line_start/line_end 用于精确的原地修改。
    """
    index: int              # 段落序号（从 0 开始）
    text: str               # 段落纯文本内容（去除了 LaTeX 命令）
    file_path: str          # 源文件路径（相对于项目根目录）
    line_start: int         # 起始行号（1-indexed）
    line_end: int           # 结束行号（1-indexed）


@dataclass
class FileNode:
    """
    文件树节点。
    用于展示项目文件结构，支持目录嵌套。
    """
    name: str               # 文件名
    path: str               # 相对路径（相对于项目根目录）
    is_dir: bool            # 是否为目录
    children: list["FileNode"] = field(default_factory=list)  # 子节点


@dataclass
class ProjectInfo:
    """
    项目信息。
    open() 方法返回的完整项目元数据。
    """
    root_path: str                          # 项目根目录绝对路径
    main_file: Optional[str]                # 主文档路径（如 main.tex）
    file_tree: list[FileNode]               # 文件树
    dependency_graph: dict[str, list[str]] = field(default_factory=dict)  # 文件依赖图


class LatexProject:
    """
    LaTeX 项目管理器。

    用法：
        project = LatexProject()
        info = project.open("/path/to/latex-project")
        paragraphs = project.get_paragraphs("main.tex")
        project.apply_change("main.tex", 0, "修改后的文本")
    """

    def __init__(self):
        self._project_info: Optional[ProjectInfo] = None
        self._logger = get_logger("project_manager")

    def open(self, path: str) -> ProjectInfo:
        """
        打开一个 LaTeX 项目目录。

        流程：
            1. 检查路径有效性
            2. 递归扫描文件树（仅保留 .tex/.bib/.cls/.sty/.pdf）
            3. 自动识别主文档（优先查找 main.tex）
            4. 解析 \\include / \\input 依赖关系

        参数：
            path: 项目目录路径

        返回：
            ProjectInfo 对象（包含文件树、主文档、依赖图）
        """
        root = Path(path).resolve()
        if not root.exists():
            raise FileNotFoundError(f"Project path not found: {path}")
        if not root.is_dir():
            raise NotADirectoryError(f"Not a directory: {path}")

        # 扫描文件树
        file_tree = self._scan_directory(root)
        # 识别主文档
        main_file = self._find_main_file(root, file_tree)
        # 构建依赖图
        dep_graph = self._build_dependency_graph(root)

        self._project_info = ProjectInfo(
            root_path=str(root),
            main_file=main_file,
            file_tree=file_tree,
            dependency_graph=dep_graph,
        )

        self._logger.info(f"Opened project: {root} | main_file={main_file}")
        return self._project_info

    def get_file_tree(self) -> list[FileNode]:
        """返回当前项目的文件树。"""
        if self._project_info is None:
            raise RuntimeError("No project opened")
        return self._project_info.file_tree

    def get_paragraphs(self, file_path: str) -> list[Paragraph]:
        """
        提取指定 .tex 文件的段落列表。

        段落分割策略：
            - 按空行（连续的两个换行符）分割
            - 每个段落记录其在源文件中的起止行号
            - 行号用于后续的精确原地修改

        参数：
            file_path: 文件路径（相对于项目根目录）

        返回：
            Paragraph 对象列表
        """
        if self._project_info is None:
            raise RuntimeError("No project opened")
        full_path = Path(self._project_info.root_path) / file_path
        if not full_path.exists():
            raise FileNotFoundError(f"File not found: {full_path}")

        with open(full_path, "r", encoding="utf-8") as f:
            content = f.read()

        paragraphs = []
        lines = content.split("\n")
        current_paragraph = []
        current_start = 1   # 1-indexed 行号

        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            if stripped == "":
                # 空行 = 段落结束符
                if current_paragraph:
                    text = "\n".join(current_paragraph)
                    paragraphs.append(
                        Paragraph(
                            index=len(paragraphs),
                            text=text,
                            file_path=str(file_path),
                            line_start=current_start,
                            line_end=i - 1,
                        )
                    )
                    current_paragraph = []
                current_start = i + 1
            else:
                current_paragraph.append(line)

        # 处理文件末尾无空行的情况
        if current_paragraph:
            text = "\n".join(current_paragraph)
            paragraphs.append(
                Paragraph(
                    index=len(paragraphs),
                    text=text,
                    file_path=str(file_path),
                    line_start=current_start,
                    line_end=len(lines),
                )
            )

        return paragraphs

    def apply_change(
        self, file_path: str, paragraph_index: int, new_text: str
    ) -> None:
        """
        原地修改指定段落的文本。

        流程：
            1. 读取源文件全部行
            2. 检测原始段落的缩进并应用到新文本
            3. 用新文本替换源文件中的对应行范围
            4. 创建 .bak 备份文件（如果 config 启用备份）

        参数：
            file_path: 文件路径（相对于项目根目录）
            paragraph_index: 段落索引（get_paragraphs 返回的列表中的位置）
            new_text: 修改后的新文本内容
        """
        if self._project_info is None:
            raise RuntimeError("No project opened")
        full_path = Path(self._project_info.root_path) / file_path
        if not full_path.exists():
            raise FileNotFoundError(f"File not found: {full_path}")

        paragraphs = self.get_paragraphs(file_path)
        if paragraph_index < 0 or paragraph_index >= len(paragraphs):
            raise IndexError(
                f"Paragraph index {paragraph_index} out of range (0-{len(paragraphs) - 1})"
            )

        para = paragraphs[paragraph_index]

        # 读取源文件全部行
        with open(full_path, "r", encoding="utf-8") as f:
            lines = f.readlines()

        # 检测并保留原始缩进
        indent = self._detect_indent(para.text)
        new_lines = [indent + line for line in new_text.split("\n")]

        # 替换指定行范围
        lines[para.line_start - 1 : para.line_end] = [
            line + "\n" for line in new_lines
        ]
        if not lines[-1].endswith("\n"):
            lines[-1] += "\n"

        # 创建备份（在写入新内容之前备份旧内容）
        if self._project_info:
            import hashlib
            backup_path = full_path.with_suffix(full_path.suffix + ".bak")
            with open(backup_path, "w", encoding="utf-8") as bf:
                with open(full_path, "r", encoding="utf-8") as rf:
                    bf.write(rf.read())

        # 写入修改后的内容
        with open(full_path, "w", encoding="utf-8") as f:
            f.writelines(lines)

        self._logger.info(
            f"Applied change: {file_path} paragraph[{paragraph_index}] lines {para.line_start}-{para.line_end}"
        )

    @staticmethod
    def _detect_indent(text: str) -> str:
        """
        检测段落的缩进前缀。
        用于在原地修改时保持与原文一致的缩进风格。
        """
        leading = len(text) - len(text.lstrip())
        return text[:leading] if leading > 0 else ""

    def _scan_directory(self, root: Path, rel_path: str = "") -> list[FileNode]:
        """
        递归扫描项目目录，构建文件树。

        参数：
            root: 项目根目录
            rel_path: 当前相对路径（递归时使用）

        返回：
            FileNode 列表（仅包含 .tex/.bib/.cls/.sty/.pdf 文件）
        """
        nodes = []
        current = root / rel_path if rel_path else root
        try:
            for entry in sorted(current.iterdir()):
                # 跳过隐藏文件和目录
                if entry.name.startswith("."):
                    continue
                rel = f"{rel_path}/{entry.name}" if rel_path else entry.name
                if entry.is_dir():
                    # 递归扫描子目录
                    nodes.append(
                        FileNode(
                            name=entry.name,
                            path=rel,
                            is_dir=True,
                            children=self._scan_directory(root, rel),
                        )
                    )
                elif entry.suffix in (".tex", ".bib", ".cls", ".sty", ".pdf"):
                    nodes.append(FileNode(name=entry.name, path=rel, is_dir=False))
        except PermissionError:
            pass  # 跳过无权限的目录
        return nodes

    def _find_main_file(
        self, root: Path, file_tree: list[FileNode]
    ) -> Optional[str]:
        """
        自动识别项目主文档。

        策略：
            1. 优先查找名为 main.tex 的文件
            2. 其次查找文件名包含 "main" 的 .tex 文件
            3. 最后返回找到的第一个 .tex 文件

        参数：
            root: 项目根目录
            file_tree: 文件树

        返回：
            主文件的相对路径，未找到则返回 None
        """
        def find_tex(nodes: list[FileNode], depth: int = 0) -> Optional[str]:
            for node in nodes:
                if not node.is_dir and node.name.endswith(".tex"):
                    if node.name.lower() == "main.tex":
                        return node.path
                    if "main" in node.name.lower():
                        return node.path
                if node.is_dir and depth < 2:
                    result = find_tex(node.children, depth + 1)
                    if result:
                        return result
            return None

        result = find_tex(file_tree)
        if result is None:
            # 兜底：返回第一个 .tex 文件
            for node in file_tree:
                if not node.is_dir and node.name.endswith(".tex"):
                    return node.path
        return result

    def _build_dependency_graph(self, root: Path) -> dict[str, list[str]]:
        """
        构建 LaTeX 文件间的依赖关系图。

        解析所有 .tex 文件中的 \\include{}、\\input{}、\\subfile{} 命令，
        建立文件间的引用关系。

        参数：
            root: 项目根目录

        返回：
            dict，key 为 .tex 文件路径，value 为其引用的文件列表
        """
        deps = {}
        for tex_file in root.rglob("*.tex"):
            if tex_file.name.startswith("."):
                continue
            rel = str(tex_file.relative_to(root))
            deps[rel] = []
            try:
                content = tex_file.read_text(encoding="utf-8")
                for line in content.split("\n"):
                    stripped = line.strip()
                    # 跳过注释行
                    if stripped.startswith("%"):
                        continue
                    # 匹配 \include{xxx}、\input{xxx}、\subfile{xxx}
                    for cmd in ["\\include{", "\\input{", "\\subfile{"]:
                        if cmd in stripped:
                            start = stripped.find(cmd) + len(cmd)
                            end = stripped.find("}", start)
                            if end > start:
                                ref = stripped[start:end].strip()
                                # 自动补全 .tex 后缀
                                if not ref.endswith(".tex"):
                                    ref += ".tex"
                                deps[rel].append(ref)
            except Exception:
                pass  # 单个文件解析失败不影响整体
        return deps


# 全局单例：项目管理器实例
_latex_project: Optional[LatexProject] = None


def get_project_manager() -> LatexProject:
    """
    获取全局唯一的 LatexProject 实例（单例模式）。
    """
    global _latex_project
    if _latex_project is None:
        _latex_project = LatexProject()
    return _latex_project
