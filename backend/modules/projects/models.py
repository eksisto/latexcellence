"""多用户项目模块请求/响应模型。"""

from pydantic import BaseModel, Field


class RenameProjectRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
