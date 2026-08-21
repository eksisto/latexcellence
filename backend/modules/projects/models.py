"""多用户项目模块请求/响应模型。"""

from typing import Literal

from pydantic import BaseModel, Field

# 协作者角色：owner 存于 projects 表，协作者仅 editor / viewer 两档
COLLABORATOR_ROLES = ("editor", "viewer")


class RenameProjectRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)


class AddCollaboratorRequest(BaseModel):
    """按用户名添加协作者（不存在则新增，已存在则更新角色）。"""

    username: str = Field(..., min_length=1, max_length=200)
    role: Literal["editor", "viewer"] = "editor"


class UpdateCollaboratorRequest(BaseModel):
    """修改某协作者在项目中的角色。"""

    role: Literal["editor", "viewer"]


class TransferProjectRequest(BaseModel):
    """转交项目 owner 给其他用户。

    keep_access 为 True 时原 owner 转交后保留为 viewer；
    为 False（默认）时原 owner 直接退出协作（前端确认）。
    """

    username: str = Field(..., min_length=1, max_length=200)
    keep_access: bool = False
