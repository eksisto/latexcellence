"""
工作区管理器。

职责：
    1. 维护 workspace_id → LatexProject 的映射
    2. 支持多浏览器并发打开不同项目，各工作区相互隔离
    3. 提供工作区创建、获取、删除、列出的统一入口
    4. 多用户模式下绑定权威存 SQLite（workspace_bindings 表，v2 P7）：
       任一副本可按绑定 root_path 重建本进程的 LatexProject 缓存

用法：
    wm = get_workspace_manager()
    project = wm.get_or_create("ws-abc-123")
    project.open("/path/to/project")

多实例语义（v2 P7，事件循环安全修订）：
    - bind/unbind/remove 同步写 DB（权威）+ 进程内缓存；
    - get_binding 以 DB 为权威实时读取，进程内 dict 仅作单用户模式存储与
      多用户模式的读缓存；
    - get_or_create 只做进程内字典操作（零 I/O），**绝不**在此重建项目：
      项目 open 是秒级全树扫描，若在 async 路由体内同步执行会冻结事件
      循环（历史缺陷，v2 P7 收口时移除）；
    - 跨副本续用改由 ensure_open_for_binding() 承担：require_workspace
      系列依赖在权限校验通过后调用它（本身已运行于 asyncio.to_thread），
      按 DB 绑定的 root_path 重开项目——A 副本打开的工作区 B 副本可直接
      续用，且重活全程在工作线程、不持有管理器锁；
    - 单用户模式不落表（无绑定语义），全部行为与历史版本一致。
"""

import threading
import time
from pathlib import Path

from backend.core.logger import get_logger
from backend.core.project_manager import LatexProject
from backend.utils.file_ops import BackupManager

# last_active_at 打点节流间隔（秒）：get_binding 位于每请求热路径，
# 不可能每次访问都写库；60s 内的重复访问合并为一次 UPDATE。
# 该列供未来活动口径使用（P3 edit_log TTL 当前仍以 op 自身活动为基准）。
_BINDING_TOUCH_INTERVAL_SECONDS = 60


def _bindings_store_available() -> bool:
    """绑定表是否可用（多用户模式且库已初始化）。"""
    from backend.core.multiuser import db
    from backend.core.config import get_config

    try:
        if not get_config().multi_user.enabled:
            return False
    except RuntimeError:
        return False
    return db.is_initialized()


class WorkspaceManager:
    """
    工作区管理器。

    每个工作区拥有独立的 LatexProject 实例，维护各自的项目状态
    （文件树、段落、MD5 缓存、撤销栈等）。
    """

    def __init__(self):
        self._workspaces: dict[str, LatexProject] = {}
        # 单用户模式的绑定存储 / 多用户模式的读缓存。
        # 权威数据在多用户模式下是 workspace_bindings 表（v2 P7）。
        self._bindings: dict[str, dict] = {}
        # 每工作区上次向 DB 刷新 last_active_at 的单调时间戳（打点节流）
        self._last_touch: dict[str, float] = {}
        self._lock = threading.RLock()
        self._logger = get_logger("workspace_manager")

    def get_or_create(self, workspace_id: str) -> LatexProject:
        """
        获取或创建指定工作区的 LatexProject 实例（零 I/O，事件循环安全）。

        只做进程内字典操作：miss 时创建**空**实例并注册。多用户模式下
        跨副本续用（本进程 miss 但 DB 有绑定）的重建由
        :meth:`ensure_open_for_binding` 负责——require_workspace 系列依赖
        已在权限校验后统一调用，路由层无需自行处理。

        参数：
            workspace_id: 工作区唯一标识符（前端生成的 UUID）

        返回：
            工作区专属的 LatexProject 实例（可能尚未 open）
        """
        with self._lock:
            if workspace_id not in self._workspaces:
                backup_mgr = BackupManager()
                project = LatexProject(backup_mgr=backup_mgr)
                self._workspaces[workspace_id] = project
                self._logger.info(
                    f"Created new workspace: {workspace_id} "
                    f"(total workspaces: {len(self._workspaces)})"
                )
            return self._workspaces[workspace_id]

    def ensure_open_for_binding(
        self, workspace_id: str, *, root_path: str | None = None
    ) -> LatexProject:
        """确保工作区实例已按绑定根目录打开（多用户跨副本续用的唯一入口）。

        - 多用户模式且绑定存在时：root_path 缺省则实时读 DB 绑定行；
          实例未打开或绑定根已变化（项目转交/迁移）时执行 ``project.open``。
        - 单用户模式 / 无绑定 / 无 root_path：原样返回实例（保持历史行为）。
        - 打开失败降级为返回空实例并告警，不抛异常（与历史重建语义一致）。

        ⚠️ 本方法含同步文件树扫描等重 I/O，**禁止在事件循环线程直接调用**；
        设计调用点是 require_workspace 依赖内部（已在 asyncio.to_thread 中）。
        不持有管理器锁执行 open，避免阻塞其他工作区的字典操作。
        """
        project = self.get_or_create(workspace_id)
        if not _bindings_store_available():
            return project
        root = root_path
        if not root:
            binding = self._load_binding_row(workspace_id)
            root = (binding or {}).get("root_path") or ""
        if not root:
            return project
        if project.is_open and project.root_path == root:
            return project
        try:
            project.open(root)
            self._logger.info(
                f"Ensured workspace {workspace_id} is open from binding root: "
                f"{root}"
            )
        except Exception as e:  # noqa: BLE001 - 打开失败降级为空实例
            self._logger.warning(
                f"Failed to open workspace {workspace_id} from {root}: {e} — "
                f"returning empty project"
            )
        return project

    def get(self, workspace_id: str) -> LatexProject | None:
        """
        获取指定工作区的 LatexProject 实例（不创建新实例）。

        参数：
            workspace_id: 工作区唯一标识符

        返回：
            LatexProject 实例，不存在则返回 None
        """
        with self._lock:
            return self._workspaces.get(workspace_id)

    def _load_binding_row(self, workspace_id: str) -> dict | None:
        """读取 DB 绑定行并裁剪为对外四字段 dict；无行返回 None。"""
        from backend.core.multiuser import db

        row = db.db_query_one(
            "SELECT user_id, project_id, owner_id, root_path, last_active_at "
            "FROM workspace_bindings WHERE workspace_id = ?",
            (workspace_id,),
        )
        if row is None:
            return None
        return {
            "user_id": row["user_id"],
            "project_id": row["project_id"],
            "owner_id": row["owner_id"],
            "root_path": row["root_path"] or None,
        }

    def _touch_binding_row(self, workspace_id: str) -> None:
        """节流刷新 last_active_at 打点（失败静默：纯观测字段不阻塞请求）。"""
        now = time.monotonic()
        last = self._last_touch.get(workspace_id, 0.0)
        if now - last < _BINDING_TOUCH_INTERVAL_SECONDS:
            return
        self._last_touch[workspace_id] = now
        try:
            from backend.core.multiuser import db

            db.db_execute(
                "UPDATE workspace_bindings SET last_active_at = ? "
                "WHERE workspace_id = ?",
                (db.utcnow(), workspace_id),
            )
        except Exception as e:  # noqa: BLE001
            self._logger.debug(f"Touch binding last_active_at failed: {e}")

    def bind(
        self,
        workspace_id: str,
        *,
        user_id: str | None = None,
        project_id: str | None = None,
        owner_id: str | None = None,
        root_path: str | None = None,
    ) -> dict:
        """
        绑定工作区与（多用户模式下的）用户/项目映射。

        单用户模式调用时可以不传或传 None（不参与权限校验），仅写进程内
        字典；多用户模式下必须传 user_id / project_id / owner_id /
        root_path，同时 upsert workspace_bindings 表（权威）。
        返回绑定信息的副本。
        """
        binding = {
            "user_id": user_id,
            "project_id": project_id,
            "owner_id": owner_id,
            "root_path": root_path,
        }
        with self._lock:
            self._bindings[workspace_id] = dict(binding)
            self._last_touch.pop(workspace_id, None)
        if _bindings_store_available() and (user_id or project_id):
            from backend.core.multiuser import db

            db.db_execute(
                "INSERT INTO workspace_bindings "
                "(workspace_id, user_id, project_id, owner_id, root_path, "
                "last_active_at) VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(workspace_id) DO UPDATE SET "
                "user_id = excluded.user_id, project_id = excluded.project_id, "
                "owner_id = excluded.owner_id, root_path = excluded.root_path, "
                "last_active_at = excluded.last_active_at",
                (
                    workspace_id,
                    user_id,
                    project_id,
                    owner_id,
                    root_path or "",
                    db.utcnow(),
                ),
            )
        return dict(binding)

    def get_binding(self, workspace_id: str) -> dict | None:
        """返回指定工作区的绑定信息（副本）；未绑定返回 None。

        多用户模式以 DB 为权威实时读取（权限判定依赖最新绑定）；进程内
        缓存仅服务单用户模式。命中时节流刷新 last_active_at 打点。
        """
        if _bindings_store_available():
            # DB 为权威：直接读表（读操作无锁竞争），命中后回填进程内缓存
            row = self._load_binding_row(workspace_id)
            if row is not None:
                with self._lock:
                    self._bindings[workspace_id] = dict(row)
                self._touch_binding_row(workspace_id)
                return row
            with self._lock:
                self._bindings.pop(workspace_id, None)
                self._last_touch.pop(workspace_id, None)
            return None
        with self._lock:
            b = self._bindings.get(workspace_id)
            return dict(b) if b else None

    def unbind(self, workspace_id: str) -> None:
        """移除指定工作区的绑定信息（不删除工作区实例）。"""
        with self._lock:
            self._bindings.pop(workspace_id, None)
            self._last_touch.pop(workspace_id, None)
        if _bindings_store_available():
            from backend.core.multiuser import db

            db.db_execute(
                "DELETE FROM workspace_bindings WHERE workspace_id = ?",
                (workspace_id,),
            )

    def refresh_project_bindings(
        self,
        project_id: str,
        *,
        owner_id: str | None = None,
        root_path: str | None = None,
    ) -> int:
        """按 project_id 批量刷新绑定行（v2 P7 多实例：项目转交后全副本生效）。

        只更新 DB 权威行——包括**其他副本**持有的 workspace（本进程
        `list_workspaces()` 只能看到本地实例，逐工作区 bind 会漏掉它们，
        留下指向旧 owner 目录的失效绑定）。本副本内存中的 LatexProject
        重开与进程内缓存同步由调用方逐工作区处理。返回更新的行数；
        单用户模式无绑定表，返回 0。
        """
        if not _bindings_store_available():
            return 0
        from backend.core.multiuser import db

        return db.db_execute(
            "UPDATE workspace_bindings SET owner_id = ?, root_path = ? "
            "WHERE project_id = ?",
            (owner_id, root_path or "", project_id),
        )

    def remove(self, workspace_id: str) -> bool:
        """
        移除指定工作区，释放资源（同时清除绑定信息）。

        参数：
            workspace_id: 工作区唯一标识符

        返回：
            True 表示成功移除，False 表示工作区不存在
        """
        existed_in_process: bool
        with self._lock:
            existed_in_process = workspace_id in self._workspaces
            self._workspaces.pop(workspace_id, None)
        # 绑定删除走统一路径（DB 权威 + 进程内缓存）
        binding_existed = self.get_binding(workspace_id) is not None
        self.unbind(workspace_id)
        existed = existed_in_process or binding_existed
        if existed:
            self._logger.info(
                f"Removed workspace: {workspace_id} "
                f"(remaining workspaces: {len(self._workspaces)})"
            )
        return existed

    def list_workspaces(self) -> list[dict]:
        """
        列出所有活跃的工作区（本进程实例 + 各自绑定的用户/项目信息）。

        多实例部署下返回的是**当前副本**持有的实例列表（admin 调试用途）。
        """
        with self._lock:
            items = list(self._workspaces.items())
        result = []
        for ws_id, project in items:
            info = {
                "workspace_id": ws_id,
                "is_open": project.is_open,
            }
            if project.is_open:
                info["root_path"] = project.root_path
            binding = self.get_binding(ws_id)
            if binding and binding.get("project_id"):
                info["user_id"] = binding.get("user_id")
                info["project_id"] = binding.get("project_id")
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
