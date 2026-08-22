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


class UploadSessionFileEntry(BaseModel):
    """分片上传 manifest 单文件条目（v2 P5）。

    chunks 必须等于 ceil(size / chunk_part_max_mb)（服务端按配置复核）。"""

    path: str = Field(..., min_length=1, max_length=1024)
    size: int = Field(..., ge=0)
    md5: str = Field(..., pattern=r"^[0-9a-f]{32}$")
    chunks: int = Field(..., ge=0)


class UploadSessionInitRequest(BaseModel):
    """创建分片上传会话（v2 P5）。

    kind=zip/7z/rar 时 files 恰好一条且扩展名须匹配；
    kind=directory 时 files 为逐个相对路径条目。"""

    name: str = Field(..., min_length=1, max_length=200)
    kind: Literal["zip", "7z", "rar", "directory"]
    total_bytes: int = Field(..., ge=1)
    files: list[UploadSessionFileEntry] = Field(..., min_length=1)
