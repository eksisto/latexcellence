"""多用户认证模块（/api/auth 与 /api/admin）的请求/响应模型。"""

from pydantic import BaseModel, Field


class RegisterRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=1)
    email: str = Field("", max_length=254)


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=1)
    remember: bool = False


class QuotaUpdateRequest(BaseModel):
    project_quota_bytes: int | None = None
    derived_quota_bytes: int | None = None
