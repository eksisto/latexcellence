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


class ResetPasswordConfirmRequest(BaseModel):
    temp_password: str = Field(..., min_length=1, max_length=128)


class CleanupDerivedDataRequest(BaseModel):
    """自助清理衍生数据的请求体：category 为四类衍生目录名之一。"""

    category: str = Field(..., min_length=1, max_length=64)


class ForgotPasswordRequest(BaseModel):
    """自助找回请求：用户名或邮箱（二选一，服务端自动判别）。"""

    identifier: str = Field(..., min_length=1, max_length=254)


class ResetPasswordRequest(BaseModel):
    """自助重置密码：邮件中的明文令牌 + 新密码。"""

    token: str = Field(..., min_length=1, max_length=128)
    new_password: str = Field(..., min_length=1, max_length=128)


class ProfileUpdateRequest(BaseModel):
    """绑定/修改邮箱：需当前密码确认；email 传空字符串表示解绑。"""

    current_password: str = Field(..., min_length=1, max_length=128)
    email: str = Field("", max_length=254)
