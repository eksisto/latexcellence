"""
LaTeX 项目管理模块。

职责：
    1. 打开 LaTeX 项目目录，扫描文件树（非隐藏 + 非编译产物黑名单）
    2. 检测 \\include / \\input / \\subfile 依赖关系
    3. 按空行分割 .tex 文件为段落，记录每段在源文件中的行号范围
    4. 原地修改：用户审核确认后，精确写回对应段落

关键设计：
    - 段落分割基于空行（LaTeX 段落惯例）
    - 修改时检测原始缩进并保留
    - 修改前通过 BackupManager 推入撤销栈，支持多步 undo/redo
    - 修改前校验文件 MD5，防止用户在审核期间手动修改文件导致冲突
"""

import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import NamedTuple

from backend.core.logger import get_logger
from backend.core.multiuser import edit_log
from backend.utils.file_ops import (
    BackupManager,
    atomic_write,
    compute_md5,
    get_backup_manager,
    safe_read,
    safe_read_lines,
)
from backend.utils.path_guard import resolve_project_path

# 编译产物后缀黑名单（editor-file-upload-design §4.3 / 决策 D7）：文件树
# 收录条件为「非隐藏 + 非编译产物」，黑名单挡住 latexmk/TeX/bibtex/
# makeindex/glossaries/beamer 常见产物刷屏。产物仍真实存在于项目（可被
# 引用、随项目下载），只是不进树；`.pdf` 刻意不进黑名单（保留编译产物
# PDF 可见可预览的现状）。匹配用 name.endswith(tuple) 而非 entry.suffix，
# 天然覆盖 `.synctex.gz` / `.run.xml` 等双后缀。
BUILD_ARTIFACT_SUFFIXES: tuple[str, ...] = (
    ".aux",
    ".log",
    ".toc",
    ".lof",
    ".lot",
    ".out",
    ".bbl",
    ".blg",
    ".bcf",
    ".run.xml",
    ".spl",
    ".fls",
    ".fdb_latexmk",
    ".synctex.gz",
    ".xdv",
    ".dvi",
    ".nav",
    ".snm",
    ".vrb",
    ".idx",
    ".ind",
    ".ilg",
    ".glo",
    ".gls",
    ".glstex",
    ".acn",
    ".acr",
    ".alg",
    ".ist",
    ".maf",
    ".bak",
)


class FileConflictError(Exception):
    """
    文件冲突异常。

    当乐观锁校验失败时抛出，表示文件在读取后被其他工作区修改。
    """

    def __init__(self, file_path: str, expected_md5: str, current_md5: str):
        self.file_path = file_path
        self.expected_md5 = expected_md5
        self.current_md5 = current_md5
        super().__init__(
            f"File {file_path} has been modified by another workspace. "
            f"Expected MD5: {expected_md5[:8]}..., got: {current_md5[:8]}... "
            f"Please refresh and try again."
        )


class WholeFileWriteResult(NamedTuple):
    """
    apply_whole_files_detailed 的统一详细结果。

    - md5s: {相对路径: 写入后的 MD5}
    - edit_op_id: 本次事务的编辑日志 op_id（多用户模式实际记账时；
      单用户模式或无变化时为 None）
    - before_contents: {相对路径: 事务锁内读取的写前全文}——与乐观锁
      校验、失败恢复使用同一份快照，供调用方（如配额超限回滚）在锁外
      安全消费，杜绝"锁外预读、锁内被改"的竞态窗口
    """

    md5s: dict[str, str]
    edit_op_id: str | None
    before_contents: dict[str, str]


@dataclass
class Paragraph:
    """
    段落数据结构。
    每个段落对应 .tex 文件中的一个 LaTeX 段落（以空行分隔）。
    index 和 line_start/line_end 用于精确的原地修改。
    """

    index: int  # 段落序号（从 0 开始）
    text: str  # 段落纯文本内容（去除了 LaTeX 命令）
    file_path: str  # 源文件路径（相对于项目根目录）
    line_start: int  # 起始行号（1-indexed）
    line_end: int  # 结束行号（1-indexed）


@dataclass
class FileNode:
    """
    文件树节点。
    用于展示项目文件结构，支持目录嵌套。
    """

    name: str  # 文件名
    path: str  # 相对路径（相对于项目根目录）
    is_dir: bool  # 是否为目录
    children: list["FileNode"] = field(default_factory=list)  # 子节点


@dataclass
class ProjectInfo:
    """
    项目信息。
    open() 方法返回的完整项目元数据。
    """

    root_path: str  # 项目根目录绝对路径
    main_file: str | None  # 主文档路径（如 main.tex）
    file_tree: list[FileNode]  # 文件树
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

    def __init__(self, backup_mgr: BackupManager | None = None):
        self._project_info: ProjectInfo | None = None
        self._logger = get_logger("project_manager")
        self._backup_mgr = backup_mgr or get_backup_manager()
        self._transaction_lock = threading.RLock()
        # 保护 _file_md5 字典读写：同一工作区可能被多个并发请求访问
        self._file_md5_lock = threading.RLock()
        # { file_path_abs: md5_hex }  记录解析时的文件哈希
        self._file_md5: dict[str, str] = {}

    def open(self, path: str) -> ProjectInfo:
        """
        打开一个 LaTeX 项目目录。

        流程：
            1. 检查路径有效性
            2. 递归扫描文件树（非隐藏 + 非编译产物黑名单，见
               _scan_directory / editor-file-upload-design §4.3）
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
        # 识别主文档：手动设置过的主文件经 .latexcellence/main_file 持久化，
        # 优先于自动识别（否则重开项目/刷新页面会静默回退 main.tex）
        main_file = self._read_persisted_main_file(root) or self._find_main_file(
            root, file_tree
        )
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

    @property
    def is_open(self) -> bool:
        """检查是否已打开项目。"""
        return self._project_info is not None

    @property
    def root_path(self) -> str:
        """返回项目根目录的绝对路径。"""
        if self._project_info is None:
            raise RuntimeError("No project opened")
        return self._project_info.root_path

    def get_file_tree(self) -> list[FileNode]:
        """返回当前项目的文件树。"""
        if self._project_info is None:
            raise RuntimeError("No project opened")
        return self._project_info.file_tree

    def refresh_file_tree(self) -> list[FileNode]:
        """重扫项目目录并就地刷新缓存的文件树。

        文件树是 open() 时的一次性扫描缓存；编辑器在项目内新建/删除
        文件后调用本方法刷新视图。只更新 file_tree，main_file 与依赖
        图保持不变（避免整项目重新 open）。
        """
        if self._project_info is None:
            raise RuntimeError("No project opened")
        root = Path(self._project_info.root_path)
        self._project_info.file_tree = self._scan_directory(root)
        return self._project_info.file_tree

    def get_main_file(self) -> str | None:
        """返回当前项目主文档的相对路径（未设置时为 None）。"""
        if self._project_info is None:
            raise RuntimeError("No project opened")
        return self._project_info.main_file

    def get_file_md5(self, file_path: str) -> str:
        """
        获取指定文件的当前 MD5 哈希值。

        用于乐观锁：前端在读取段落时获取 MD5，应用修改时传递回来。
        如果文件在两次操作之间被其他工作区修改，apply_change 会检测到冲突。

        参数：
            file_path: 文件路径（相对于项目根目录）

        返回：
            文件的 MD5 十六进制字符串
        """
        if self._project_info is None:
            raise RuntimeError("No project opened")
        full_path = resolve_project_path(
            Path(self._project_info.root_path),
            file_path,
        )
        if not full_path.exists():
            # 错误消息使用相对路径，避免向用户暴露项目根目录绝对路径
            raise FileNotFoundError(f"File not found: {file_path}")

        md5 = compute_md5(full_path)
        abs_path = str(full_path)
        with self._file_md5_lock:
            self._file_md5[abs_path] = md5
        return md5

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
        full_path = resolve_project_path(
            Path(self._project_info.root_path),
            file_path,
            allowed_suffixes=(".tex",),
        )
        if not full_path.exists():
            # 错误消息使用相对路径，避免向用户暴露项目根目录绝对路径
            raise FileNotFoundError(f"File not found: {file_path}")

        content = safe_read(full_path)

        # 记录解析时的文件 MD5，供后续 apply_change 校验
        # 使用 compute_md5（二进制读取）与 apply_change 中保持一致
        md5 = compute_md5(full_path)
        with self._file_md5_lock:
            self._file_md5[str(full_path)] = md5

        paragraphs = []
        lines = content.split("\n")
        current_paragraph = []
        current_start = 1  # 1-indexed 行号

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
        self,
        file_path: str,
        paragraph_index: int,
        new_text: str,
        expected_md5: str | None = None,
    ) -> str | None:
        """
        原地修改指定段落的文本。

        流程：
            1. 校验文件 MD5（与预期一致，防止并发冲突）
            2. 将文件当前内容推入撤销栈
            3. 读取源文件全部行
            4. 检测原始段落缩进并应用到新文本
            5. 用新文本替换源文件中的对应行范围
            6. 原子写入文件

        参数：
            file_path: 文件路径（相对于项目根目录）
            paragraph_index: 段落索引（get_paragraphs 返回的列表中的位置）
            new_text: 修改后的新文本内容
            expected_md5: 期望的文件 MD5（乐观锁版本号），为 None 时跳过校验

        返回：
            多用户模式下本次写入对应的编辑日志 op_id（供配额回滚时撤销
            记账，方案 §8.4）；单用户模式或无变化时返回 None
        """
        if self._project_info is None:
            raise RuntimeError("No project opened")
        full_path = resolve_project_path(
            Path(self._project_info.root_path),
            file_path,
        )
        if not full_path.exists():
            # 错误消息使用相对路径，避免向用户暴露项目根目录绝对路径
            raise FileNotFoundError(f"File not found: {file_path}")

        abs_path = str(full_path)

        # 乐观锁校验：比较预期 MD5 与文件当前 MD5
        # 优先使用调用方传入的 expected_md5（跨工作区乐观锁）
        # 否则退回到实例内部缓存的 MD5（单工作区内校验）
        with self._file_md5_lock:
            cached_md5 = self._file_md5.get(abs_path)
        md5_to_check = expected_md5 if expected_md5 is not None else cached_md5
        if md5_to_check is not None:
            current_md5 = compute_md5(full_path)
            if current_md5 != md5_to_check:
                raise FileConflictError(
                    file_path=file_path,
                    expected_md5=md5_to_check,
                    current_md5=current_md5,
                )

        paragraphs = self.get_paragraphs(file_path)
        if paragraph_index < 0 or paragraph_index >= len(paragraphs):
            raise IndexError(
                f"Paragraph index {paragraph_index} out of range (0-{len(paragraphs) - 1})"
            )

        para = paragraphs[paragraph_index]

        # 将当前内容推入撤销栈（在读取源文件前）
        self._backup_mgr.push(full_path)

        # 读取源文件全部行
        lines = safe_read_lines(full_path)

        # 记录写前全文（多用户模式反向补丁记账用，方案 §8.2）
        before_content = "".join(lines)

        # 检测并保留原始缩进
        indent = self._detect_indent(para.text)
        new_lines_list = [indent + line for line in new_text.split("\n")]

        # 替换指定行范围
        lines[para.line_start - 1 : para.line_end] = [
            line + "\n" for line in new_lines_list
        ]
        content = "".join(lines)
        if not content.endswith("\n"):
            content += "\n"

        # 原子写入
        atomic_write(full_path, content)

        # 更新 MD5 缓存
        md5 = compute_md5(full_path)
        with self._file_md5_lock:
            self._file_md5[abs_path] = md5

        # 多用户模式：写入成功后记反向补丁 op（单用户模式内部直接跳过）
        op_id = edit_log.record_write(
            self._project_info.root_path,
            [(file_path, before_content, content)],
        )

        self._logger.info(
            f"Applied change: {file_path} paragraph[{paragraph_index}] "
            f"lines {para.line_start}-{para.line_end}"
        )
        return op_id

    def apply_whole_files(
        self,
        updates: dict[str, str],
        *,
        expected_md5s: dict[str, str] | None = None,
        allowed_suffixes: tuple[str, ...] | None = None,
        return_edit_op_id: bool = False,
    ) -> dict[str, str] | tuple[dict[str, str], str | None]:
        """
        以事务方式批量替换多个文件的完整内容（兼容旧契约的薄包装）。

        实现统一委托 apply_whole_files_detailed；本方法仅按历史返回契约
        解包结果，供既有调用方（bib_manager / style_modify / projects
        配额回滚等）零改动运行。

        参数：
            updates: {相对路径: 完整新内容}
            expected_md5s: 可选的乐观锁 {相对路径: 期望 MD5}
            allowed_suffixes: 可选扩展名白名单
            return_edit_op_id: 为 True 时额外返回本次写入的编辑日志 op_id
                （多用户模式实际记账时；单用户模式或无变化时为 None），
                供调用方在配额超限回滚后撤销对应记账（方案 §8.4）

        返回：
            {相对路径: 写入后的 MD5}；return_edit_op_id=True 时返回
            (上述字典, edit_op_id | None)
        """
        result = self.apply_whole_files_detailed(
            updates,
            expected_md5s=expected_md5s,
            allowed_suffixes=allowed_suffixes,
        )
        if return_edit_op_id:
            return result.md5s, result.edit_op_id
        return result.md5s

    def apply_whole_files_detailed(
        self,
        updates: dict[str, str],
        *,
        expected_md5s: dict[str, str] | None = None,
        allowed_suffixes: tuple[str, ...] | None = None,
    ) -> WholeFileWriteResult:
        """
        apply_whole_files 的详细事务实现。

        提交前在事务锁内逐文件校验存在性、读取写前全文并校验 MD5 与
        expected_md5s 一致；任一文件校验失败或写入失败时，已写入文件恢复到
        事务开始前的内容。每个文件在写入前会推入 BackupManager，保留常规
        undo 能力。写前全文与乐观锁/失败恢复共用同一份锁内快照并随结果
        返回（before_contents），调用方无须再在锁外自行预读。

        参数：
            updates: {相对路径: 完整新内容}
            expected_md5s: 可选的乐观锁 {相对路径: 期望 MD5}
            allowed_suffixes: 可选扩展名白名单

        返回：
            WholeFileWriteResult(md5s, edit_op_id, before_contents)

        异常：
            FileNotFoundError: 任一目标文件不存在（锁内存在性检查时抛出）
            FileConflictError: 乐观锁校验失败
        """
        if not self._project_info:
            raise RuntimeError("No project opened")
        root = Path(self._project_info.root_path).resolve()
        with self._transaction_lock:
            prepared: list[tuple[str, Path, str, str]] = []
            for file_path, content in updates.items():
                full_path = resolve_project_path(
                    root,
                    file_path,
                    allowed_suffixes=allowed_suffixes,
                    allow_symlink=False,
                )
                if not full_path.exists() or not full_path.is_file():
                    raise FileNotFoundError(f"File not found: {file_path}")
                try:
                    current = full_path.read_text(encoding="utf-8")
                except UnicodeDecodeError:
                    # 与 file_ops.safe_read 同语义（UTF-8 解码 + 通用换行
                    # 归一化），但保持标准 UnicodeDecodeError 类型原样上抛，
                    # 供调用方区分映射（如编辑器保存 → invalid_encoding 400）
                    # 并以相对路径自行构造面向用户的错误消息；原始解码错误
                    # 不含路径，无多用户脱敏问题。
                    raise
                if expected_md5s is not None:
                    expected = expected_md5s.get(file_path)
                    if expected is not None:
                        current_md5 = compute_md5(full_path)
                        if current_md5 != expected:
                            raise FileConflictError(
                                file_path=file_path,
                                expected_md5=expected,
                                current_md5=current_md5,
                            )
                prepared.append((file_path, full_path, current, content))

            written: list[Path] = []
            try:
                for _, full_path, _, content in prepared:
                    self._backup_mgr.push(full_path)
                    atomic_write(full_path, content)
                    written.append(full_path)
            except Exception as exc:
                self._logger.error(
                    "Batch write failed, restoring %d file(s): %s",
                    len(prepared),
                    exc,
                )
                for _, full_path, current, _ in prepared:
                    try:
                        atomic_write(full_path, current)
                    except Exception as restore_error:
                        self._logger.error(
                            "Failed to restore %s after batch error: %s",
                            full_path,
                            restore_error,
                        )
                raise

            result: dict[str, str] = {}
            for file_path, full_path, _, _ in prepared:
                md5 = compute_md5(full_path)
                with self._file_md5_lock:
                    self._file_md5[str(full_path)] = md5
                result[file_path] = md5

            # 多用户模式：整个事务的多文件写共享同一 op_group_id，
            # 一次撤销整组回滚（方案 §8.1）；单用户模式内部直接跳过
            edit_op_id = edit_log.record_write(
                self._project_info.root_path,
                [
                    (file_path, before, content)
                    for file_path, _, before, content in prepared
                ],
            )

            self._logger.info(
                "Applied whole-file batch: %d file(s)",
                len(prepared),
            )
            return WholeFileWriteResult(
                md5s=result,
                edit_op_id=edit_op_id,
                before_contents={
                    file_path: before for file_path, _, before, _ in prepared
                },
            )

    def undo(self, file_path: str) -> bool:
        """
        撤销对指定文件最近一次的修改。

        参数：
            file_path: 文件路径（相对于项目根目录）

        返回：
            True 表示成功撤销，False 表示无可撤销操作
        """
        if self._project_info is None:
            raise RuntimeError("No project opened")
        full_path = resolve_project_path(
            Path(self._project_info.root_path),
            file_path,
        )
        result = self._backup_mgr.undo(full_path)
        if result:
            abs_path = str(full_path)
            md5 = compute_md5(full_path)
            with self._file_md5_lock:
                self._file_md5[abs_path] = md5
        return result

    def redo(self, file_path: str) -> bool:
        """
        重做对指定文件最近一次撤销的操作。

        参数：
            file_path: 文件路径（相对于项目根目录）

        返回：
            True 表示成功重做，False 表示无可重做操作
        """
        if self._project_info is None:
            raise RuntimeError("No project opened")
        full_path = resolve_project_path(
            Path(self._project_info.root_path),
            file_path,
        )
        result = self._backup_mgr.redo(full_path)
        if result:
            abs_path = str(full_path)
            md5 = compute_md5(full_path)
            with self._file_md5_lock:
                self._file_md5[abs_path] = md5
        return result

    def read_project_file(self, file_path: str) -> str:
        """
        读取项目内文件的当前全文（供写前捕获回滚内容）。

        参数：
            file_path: 文件路径（相对于项目根目录）

        返回：
            文件全文文本
        """
        if self._project_info is None:
            raise RuntimeError("No project opened")
        full_path = resolve_project_path(
            Path(self._project_info.root_path),
            file_path,
        )
        return safe_read(full_path)

    def restore_project_file(self, file_path: str, content: str) -> str:
        """
        直接把文件内容恢复为给定全文（不走撤销通道、不记编辑日志 op）。

        供多用户模式写回超限的配额回滚使用（方案 §8.4：避免误回退他人
        修改、不污染撤销栈）。单用户模式下同样只是原样恢复内容。

        参数：
            file_path: 文件路径（相对于项目根目录）
            content: 恢复用的全文内容

        返回：
            恢复后文件的 MD5
        """
        if self._project_info is None:
            raise RuntimeError("No project opened")
        full_path = resolve_project_path(
            Path(self._project_info.root_path),
            file_path,
        )
        atomic_write(full_path, content)
        md5 = compute_md5(full_path)
        with self._file_md5_lock:
            self._file_md5[str(full_path)] = md5
        return md5

    def set_main_file(self, file_path: str) -> None:
        """
        手动设置项目主文档路径。

        同时持久化到 <root>/.latexcellence/main_file：手动选择必须跨项目
        重开（页面刷新直达恢复 / 后端重启 / 关闭再打开）保持有效，否则
        open() 的自动识别（优先 main.tex）会静默覆盖用户选择。持久化
        失败仅记警告——内存中的设置仍然生效，行为不劣于不持久化。

        参数：
            file_path: 主文件相对路径
        """
        if self._project_info is None:
            raise RuntimeError("No project opened")
        full_path = resolve_project_path(
            Path(self._project_info.root_path),
            file_path,
            allowed_suffixes=(".tex",),
        )
        if not full_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")
        self._project_info.main_file = file_path
        self._persist_main_file(Path(self._project_info.root_path), file_path)
        self._logger.info(f"Main file set to: {file_path}")

    # 手动主文件持久化标记（隐藏目录：_scan_directory 与大纲/符号扫描
    # 均跳过点开头条目，不会进文件树或参与任何扫描）
    _MAIN_FILE_MARKER_DIR = ".latexcellence"
    _MAIN_FILE_MARKER_NAME = "main_file"
    _MARKER_GITIGNORE_LINE = ".latexcellence/"

    def _persist_main_file(self, root: Path, file_path: str) -> None:
        """把手动主文件写入项目内标记文件（原子写，失败仅记警告）。"""
        try:
            marker_dir = root / self._MAIN_FILE_MARKER_DIR
            marker_dir.mkdir(parents=True, exist_ok=True)
            atomic_write(marker_dir / self._MAIN_FILE_MARKER_NAME, file_path + "\n")
        except OSError as e:
            self._logger.warning("Persist main file marker failed: %s", e)
            return
        self._ensure_marker_ignored(root)

    def _ensure_marker_ignored(self, root: Path) -> None:
        """确保项目 .gitignore 排除标记目录（best-effort，失败仅记警告）。

        项目自带 .gitignore 时 git_manage 的 init 不会写入模板，标记文件
        会以未跟踪条目出现在版本管理页，故在写入标记后主动兜底补写。
        无 .gitignore 时仅在项目根已有仓库（.git 存在）才创建最小条目；
        无仓库时不创建——留给 git_manage init 写入完整 LaTeX 模板
        （已含本排除项），避免一个只含本条目的 .gitignore 抑制完整模板。
        """
        gitignore = root / ".gitignore"
        try:
            if gitignore.is_file():
                text = gitignore.read_text(encoding="utf-8")
                if any(
                    line.strip().strip("/") == self._MAIN_FILE_MARKER_DIR
                    for line in text.splitlines()
                ):
                    return
                if text.endswith("\n\n"):
                    separator = ""
                elif text.endswith("\n"):
                    separator = "\n"
                else:
                    separator = "\n\n"
                atomic_write(
                    gitignore,
                    f"{text}{separator}{self._MARKER_GITIGNORE_LINE}\n",
                )
            elif (root / ".git").exists():
                atomic_write(gitignore, f"{self._MARKER_GITIGNORE_LINE}\n")
        except OSError as e:
            self._logger.warning("Ensure .gitignore excludes marker dir failed: %s", e)

    def _read_persisted_main_file(self, root: Path) -> str | None:
        """读取持久化的手动主文件；缺失/非法/指向文件不存在时返回 None。

        标记内容经 path_guard 白名单校验（仅 .tex、拒绝穿越与符号链接），
        指向文件必须真实存在——上传压缩包夹带的恶意/陈旧标记一律安全
        回落到自动识别。
        """
        marker = root / self._MAIN_FILE_MARKER_DIR / self._MAIN_FILE_MARKER_NAME
        try:
            if not marker.is_file():
                return None
            value = marker.read_text(encoding="utf-8").strip()
            if not value:
                return None
            resolve_project_path(root, value, allowed_suffixes=(".tex",))
            if not (root / value).is_file():
                return None
            return value
        except (OSError, ValueError):
            return None

    def can_undo(self, file_path: str) -> bool:
        """检查指定文件是否有可撤销的操作。"""
        if self._project_info is None:
            return False
        full_path = resolve_project_path(
            Path(self._project_info.root_path),
            file_path,
        )
        return self._backup_mgr.can_undo(full_path)

    def can_redo(self, file_path: str) -> bool:
        """检查指定文件是否有可重做的操作。"""
        if self._project_info is None:
            return False
        full_path = resolve_project_path(
            Path(self._project_info.root_path),
            file_path,
        )
        return self._backup_mgr.can_redo(full_path)

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

        文件节点收录条件为「非隐藏 + 非编译产物」（editor-file-upload-design
        §4.3 / 决策 D7）：任意格式资源（zip 导入或文件树上传）均在树中可见，
        仅编译产物黑名单（BUILD_ARTIFACT_SUFFIXES）不进树；目录仍递归全收。

        参数：
            root: 项目根目录
            rel_path: 当前相对路径（递归时使用）

        返回：
            FileNode 列表（非隐藏 + 非编译产物的全量文件与目录）
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
                elif not entry.name.endswith(BUILD_ARTIFACT_SUFFIXES):
                    nodes.append(FileNode(name=entry.name, path=rel, is_dir=False))
        except PermissionError:
            pass  # 跳过无权限的目录
        return nodes

    def _find_main_file(self, root: Path, file_tree: list[FileNode]) -> str | None:
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

        def find_tex(nodes: list[FileNode], depth: int = 0) -> str | None:
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
