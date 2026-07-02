"""
项目管理 API 路由。

提供项目打开、文件树查询、段落提取、原地修改、撤销/重做等接口。
"""

from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from backend.core.project_manager import get_project_manager
from backend.core.config import get_config, update_and_save

router = APIRouter(prefix="/api/project", tags=["project"])


class OpenProjectRequest(BaseModel):
    path: str


class ApplyChangeRequest(BaseModel):
    file_path: str
    paragraph_index: int
    new_text: str


class FileActionRequest(BaseModel):
    file_path: str


@router.post("/open")
async def open_project(request: OpenProjectRequest):
    """
    打开一个 LaTeX 项目目录。
    返回项目信息（文件树、主文档、依赖图）。
    同时将路径持久化保存到 config.json，下次启动时可自动恢复。
    """
    project = get_project_manager()
    try:
        info = project.open(request.path)

        # 将打开的项目路径保存到 config.json，实现持久化
        try:
            update_and_save(project__default_open_dir=info.root_path)
        except Exception:
            pass  # 持久化失败不应影响打开流程

        return {
            "root_path": info.root_path,
            "main_file": info.main_file,
            "file_tree": _serialize_file_tree(info.file_tree),
            "dependency_graph": info.dependency_graph,
        }
    except (FileNotFoundError, NotADirectoryError) as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/last-opened")
async def get_last_opened():
    """
    获取上次打开的项目目录路径（从 config.json 读取 default_open_dir）。
    前端可用于启动时自动恢复项目。
    """
    cfg = get_config()
    return {
        "path": cfg.project.default_open_dir,
        "exists": bool(cfg.project.default_open_dir),
    }


@router.get("/file-tree")
async def get_file_tree():
    """获取当前打开项目的文件树。"""
    project = get_project_manager()
    try:
        tree = project.get_file_tree()
        return {"file_tree": _serialize_file_tree(tree)}
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/paragraphs")
async def get_paragraphs(file_path: str = Query(..., description="文件路径（相对于项目根目录）")):
    """获取指定 .tex 文件的段落列表。"""
    project = get_project_manager()
    try:
        paragraphs = project.get_paragraphs(file_path)
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
        }
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/apply-change")
async def apply_change(request: ApplyChangeRequest):
    """原地修改指定段落的文本。"""
    project = get_project_manager()
    try:
        project.apply_change(
            request.file_path, request.paragraph_index, request.new_text
        )
        return {"status": "ok"}
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except IndexError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/undo")
async def undo_change(request: FileActionRequest):
    """撤销对指定文件最近一次的修改。"""
    project = get_project_manager()
    try:
        result = project.undo(request.file_path)
        return {"status": "ok", "undone": result}
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/redo")
async def redo_change(request: FileActionRequest):
    """重做对指定文件最近一次撤销的操作。"""
    project = get_project_manager()
    try:
        result = project.redo(request.file_path)
        return {"status": "ok", "redone": result}
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/can-undo")
async def can_undo(file_path: str = Query(...)):
    """检查指定文件是否有可撤销的操作。"""
    project = get_project_manager()
    return {"can_undo": project.can_undo(file_path)}


@router.get("/can-redo")
async def can_redo(file_path: str = Query(...)):
    """检查指定文件是否有可重做的操作。"""
    project = get_project_manager()
    return {"can_redo": project.can_redo(file_path)}


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
