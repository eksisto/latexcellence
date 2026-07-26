"""
工作区管理器。

职责：
    1. 维护 workspace_id → LatexProject 的映射
    2. 支持多浏览器并发打开不同项目，各工作区相互隔离
    3. 提供工作区创建、获取、删除、列出的统一入口

用法：
    wm = get_workspace_manager()
    project = wm.get_or_create("ws-abc-123")
    project.open("/path/to/project")
"""

from backend.core.logger import get_logger
from backend.core.project_manager import LatexProject
from backend.utils.file_ops import BackupManager


class WorkspaceManager:
    """
    工作区管理器。

    每个工作区拥有独立的 LatexProject 实例，维护各自的项目状态
    （文件树、段落、MD5 缓存、撤销栈等）。
    """

    def __init__(self):
        self._workspaces: dict[str, LatexProject] = {}
        self._logger = get_logger("workspace_manager")

    def get_or_create(self, workspace_id: str) -> LatexProject:
        """
        获取或创建指定工作区的 LatexProject 实例。

        参数：
            workspace_id: 工作区唯一标识符（前端生成的 UUID）

        返回：
            工作区专属的 LatexProject 实例
        """
        if workspace_id not in self._workspaces:
            backup_mgr = BackupManager()
            project = LatexProject(backup_mgr=backup_mgr)
            self._workspaces[workspace_id] = project
            self._logger.info(
                f"Created new workspace: {workspace_id} "
                f"(total workspaces: {len(self._workspaces)})"
            )
        return self._workspaces[workspace_id]

    def get(self, workspace_id: str) -> LatexProject | None:
        """
        获取指定工作区的 LatexProject 实例（不创建新实例）。

        参数：
            workspace_id: 工作区唯一标识符

        返回：
            LatexProject 实例，不存在则返回 None
        """
        return self._workspaces.get(workspace_id)

    def remove(self, workspace_id: str) -> bool:
        """
        移除指定工作区，释放资源。

        参数：
            workspace_id: 工作区唯一标识符

        返回：
            True 表示成功移除，False 表示工作区不存在
        """
        if workspace_id in self._workspaces:
            del self._workspaces[workspace_id]
            self._logger.info(
                f"Removed workspace: {workspace_id} "
                f"(remaining workspaces: {len(self._workspaces)})"
            )
            return True
        return False

    def list_workspaces(self) -> list[dict]:
        """
        列出所有活跃的工作区。

        返回：
            包含 workspace_id 和项目路径的列表
        """
        result = []
        for ws_id, project in self._workspaces.items():
            info = {
                "workspace_id": ws_id,
                "is_open": project.is_open,
            }
            if project.is_open:
                info["root_path"] = project.root_path
            result.append(info)
        return result


_workspace_manager: WorkspaceManager | None = None


def get_workspace_manager() -> WorkspaceManager:
    """
    获取全局唯一的 WorkspaceManager 实例（单例模式）。
    """
    global _workspace_manager
    if _workspace_manager is None:
        _workspace_manager = WorkspaceManager()
    return _workspace_manager


def get_project_manager_for_workspace(workspace_id: str) -> LatexProject:
    """
    获取指定工作区专属的 LatexProject 实例。

    这是各模块获取 project manager 的标准入口。
    替换原有的 get_project_manager() 单例模式。
    """
    return get_workspace_manager().get_or_create(workspace_id)
