"""
多用户模式基础设施包（backend/core/multiuser/）。

分层说明（与既有 backend/core 平级，专属于多用户模式）：

    db.py            SQLite 初始化、连接管理与低层查询入口（同步、线程安全）
    userspace.py     用户空间目录布局
    auth.py          认证核心：密码哈希、会话令牌、用户管理、登录限流
    dependencies.py  FastAPI 认证依赖（get_current_user / require_admin）
    maintenance_lease.py / pending_interactions.py / operation_contexts.py
                     多实例状态外置（v2 P7）：维护选主租约、跨副本待定交互、
                     逐段检查操作上下文

多用户相关业务路由放在 backend/modules/auth/（/api/auth 与 /api/admin），
CLI 管理工具在 backend/manage.py。各模块实现应遵循：同步 I/O（含 SQLite）
一律通过 asyncio.to_thread 调度，禁止阻塞事件循环。
"""
