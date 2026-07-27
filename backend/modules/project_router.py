"""
项目管理 API 路由。

提供项目打开、文件树查询、段落提取、原地修改、撤销/重做等接口。
"""

from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from backend.core.project_manager import FileConflictError
from backend.core.workspace_manager import (
    get_project_manager_for_workspace,
    get_workspace_manager,
)

router = APIRouter(prefix="/api/project", tags=["project"])


class OpenProjectRequest(BaseModel):
    path: str


class ApplyChangeRequest(BaseModel):
    file_path: str
    paragraph_index: int
    new_text: str
    expected_md5: str | None = None


class FileActionRequest(BaseModel):
    file_path: str


@router.post("/open")
async def open_project(
    request: OpenProjectRequest,
    workspace_id: str = Query(..., description="工作区唯一标识符"),
):
    """
    打开一个 LaTeX 项目目录。
    返回项目信息（文件树、主文档、依赖图）。
    同时将路径持久化保存到 config.json，下次启动时可自动恢复。
    """
    project = get_project_manager_for_workspace(workspace_id)
    try:
        info = project.open(request.path)

        return {
            "root_path": info.root_path,
            "main_file": info.main_file,
            "file_tree": _serialize_file_tree(info.file_tree),
            "dependency_graph": info.dependency_graph,
        }
    except (FileNotFoundError, NotADirectoryError) as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/browse")
async def browse_directory(
    path: str = Query(default="", description="目录路径，默认为磁盘根目录")
):
    """
    浏览指定路径下的子目录列表。
    供前端文件夹选择器使用，返回子目录名称和完整路径。
    """
    target = Path(path) if path else Path.cwd()
    try:
        if not target.exists():
            raise HTTPException(status_code=400, detail=f"路径不存在: {path}")
        if not target.is_dir():
            raise HTTPException(status_code=400, detail=f"不是目录: {path}")
        entries = []
        for entry in sorted(target.iterdir()):
            if entry.name.startswith("."):
                continue
            if entry.is_dir():
                entries.append(
                    {
                        "name": entry.name,
                        "path": str(entry.resolve()),
                    }
                )
        return {
            "current": str(target.resolve()),
            "parent": str(target.parent.resolve()) if target.parent != target else None,
            "directories": entries,
        }
    except PermissionError:
        raise HTTPException(status_code=403, detail=f"无权访问目录: {path}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/file-tree")
async def get_file_tree(workspace_id: str = Query(..., description="工作区唯一标识符")):
    """获取当前打开项目的文件树。"""
    project = get_project_manager_for_workspace(workspace_id)
    try:
        tree = project.get_file_tree()
        return {"file_tree": _serialize_file_tree(tree)}
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/set-main-file")
async def set_main_file(
    request: FileActionRequest,
    workspace_id: str = Query(..., description="工作区唯一标识符"),
):
    """手动设置项目的主 LaTeX 文件。"""
    project = get_project_manager_for_workspace(workspace_id)
    try:
        project.set_main_file(request.file_path)
        return {"status": "ok", "main_file": request.file_path}
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/paragraphs")
async def get_paragraphs(
    file_path: str = Query(..., description="文件路径（相对于项目根目录）"),
    workspace_id: str = Query(..., description="工作区唯一标识符"),
):
    """获取指定 .tex 文件的段落列表。"""
    project = get_project_manager_for_workspace(workspace_id)
    try:
        paragraphs = project.get_paragraphs(file_path)
        # 同时返回文件 MD5，供前端乐观锁使用
        file_md5 = project.get_file_md5(file_path)
        return {
            "paragraphs": [
                {
                    "index": p.index,
                    "text": p.text,
                    "file_path": p.file_path,
                    "line_start": p.line_start,
                    "line_end": p.line_end,
                }
                for p in paragraphs
            ],
            "file_path": file_path,
            "total": len(paragraphs),
            "file_md5": file_md5,
        }
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/file-md5")
async def get_file_md5(
    file_path: str = Query(..., description="文件路径（相对于项目根目录）"),
    workspace_id: str = Query(..., description="工作区唯一标识符"),
):
    """获取指定文件的当前 MD5 哈希值（用于乐观锁版本校验）。"""
    project = get_project_manager_for_workspace(workspace_id)
    try:
        md5 = project.get_file_md5(file_path)
        return {"file_path": file_path, "md5": md5}
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/apply-change")
async def apply_change(
    request: ApplyChangeRequest,
    workspace_id: str = Query(..., description="工作区唯一标识符"),
):
    """原地修改指定段落的文本。"""
    project = get_project_manager_for_workspace(workspace_id)
    try:
        project.apply_change(
            request.file_path,
            request.paragraph_index,
            request.new_text,
            expected_md5=request.expected_md5,
        )
        return {"status": "ok"}
    except FileConflictError as e:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "file_conflict",
                "message": str(e),
                "file_path": e.file_path,
                "expected_md5": e.expected_md5,
                "current_md5": e.current_md5,
            },
        )
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except IndexError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/undo")
async def undo_change(
    request: FileActionRequest,
    workspace_id: str = Query(..., description="工作区唯一标识符"),
):
    """撤销对指定文件最近一次的修改。"""
    project = get_project_manager_for_workspace(workspace_id)
    try:
        result = project.undo(request.file_path)
        return {"status": "ok", "undone": result}
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/redo")
async def redo_change(
    request: FileActionRequest,
    workspace_id: str = Query(..., description="工作区唯一标识符"),
):
    """重做对指定文件最近一次撤销的操作。"""
    project = get_project_manager_for_workspace(workspace_id)
    try:
        result = project.redo(request.file_path)
        return {"status": "ok", "redone": result}
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/can-undo")
async def can_undo(
    file_path: str = Query(...),
    workspace_id: str = Query(..., description="工作区唯一标识符"),
):
    """检查指定文件是否有可撤销的操作。"""
    project = get_project_manager_for_workspace(workspace_id)
    return {"can_undo": project.can_undo(file_path)}


@router.get("/can-redo")
async def can_redo(
    file_path: str = Query(...),
    workspace_id: str = Query(..., description="工作区唯一标识符"),
):
    """检查指定文件是否有可重做的操作。"""
    project = get_project_manager_for_workspace(workspace_id)
    return {"can_redo": project.can_redo(file_path)}


@router.delete("/workspace/{workspace_id}")
async def close_workspace(workspace_id: str):
    """关闭指定工作区，释放相关资源。"""
    wm = get_workspace_manager()
    removed = wm.remove(workspace_id)
    return {"status": "ok", "removed": removed}


@router.get("/workspaces")
async def list_workspaces():
    """列出所有活跃的工作区。"""
    wm = get_workspace_manager()
    return {"workspaces": wm.list_workspaces()}


def _serialize_file_tree(nodes):
    """将 FileNode 列表序列化为 JSON 兼容格式。"""
    result = []
    for node in nodes:
        item = {
            "name": node.name,
            "path": node.path,
            "is_dir": node.is_dir,
        }
        if node.children:
            item["children"] = _serialize_file_tree(node.children)
        result.append(item)
    return result
