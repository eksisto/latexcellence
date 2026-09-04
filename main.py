"""
LaTeXcellence — 统一入口文件

用法：
    python main.py config.json              # 使用指定配置文件启动
    python main.py                          # 使用默认 config.json 启动

工作模式：
    - 开发模式（dev_mode=true）: 不挂载静态文件，前端需单独启动 Vite dev server
    - 生产模式（dev_mode=false）: 自动挂载 static/ 目录下的 Vite 构建产物
"""

import asyncio
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse
from openai import APIConnectionError, APIStatusError, APITimeoutError
from pydantic import BaseModel, ValidationError
import uvicorn

from backend.core.config import (
    LLM_ROLE_KEYS,
    load_config,
    get_config,
    update_config_from_dict,
)
from backend.core.logger import setup_logging, get_logger
from backend.core.model_router import (
    get_client_for,
    get_client_for_profile,
    resolve_profile_by_id,
    resolve_role_routes,
)
from backend.utils.spa_static_files import SPAStaticFiles
from backend.core.multiuser.dependencies import (
    require_admin_if_multiuser,
    require_login_if_multiuser,
)
from backend.core.multiuser.llm_governance import (
    admission_exempt_scope,
    register_governance_handler,
)
from backend.core.security_middleware import AccessSecurityMiddleware
from backend.modules.project_router import router as project_router
from backend.modules.typo_check.router import router as typo_check_router
from backend.modules.expression_opt.router import router as expression_opt_router
from backend.modules.template_gen.router import router as template_gen_router
from backend.modules.template_library.router import router as template_library_router
from backend.modules.formula_ocr.router import router as formula_ocr_router
from backend.modules.table_ocr.router import router as table_ocr_router
from backend.modules.code_review.router import router as code_review_router
from backend.modules.compile.router import router as compile_router
from backend.modules.git_diff.router import router as git_diff_router
from backend.modules.git_manage.router import router as git_manage_router
from backend.modules.format_converter.router import (
    router as format_converter_router,
)
from backend.modules.style_modify.router import router as style_modify_router
from backend.modules.terminology_check.router import router as terminology_check_router
from backend.modules.paragraph_translate.router import (
    router as paragraph_translate_router,
)
from backend.modules.writing_chat.router import router as writing_chat_router
from backend.modules.bib_manager.router import router as bib_manager_router
from backend.modules.plot_gen.router import router as plot_gen_router
from backend.modules.auth.router import (
    admin_router as auth_admin_router,
    auth_router,
)
from backend.modules.projects.router import router as projects_router
from backend.utils.search_router import router as search_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    FastAPI 生命周期钩子。
    - 启动时：记录服务器启动日志，启动沙箱清理后台任务
    - 关闭时：取消清理任务，记录服务器关闭日志
    """
    logger = get_logger("main")
    logger.info("Starting LaTeXcellence server")

    # 多用户模式：初始化 SQLite 数据库并引导首个管理员账号（阻塞 I/O 走线程池）
    if get_config().multi_user.enabled:
        from backend.core.multiuser import auth as multiuser_auth
        from backend.core.multiuser.db import init_db

        await asyncio.to_thread(init_db)
        admin = await asyncio.to_thread(multiuser_auth.ensure_bootstrap_admin)
        if admin is not None:
            logger.info(
                "Bootstrap admin '%s' ready — multi-user mode is live",
                admin["username"],
            )

    from backend.modules.template_gen.cleanup import cleanup_loop
    from backend.modules.plot_gen.cleanup import cleanup_loop as plot_cleanup_loop
    from backend.modules.style_modify.cleanup import (
        cleanup_loop as style_modify_cleanup_loop,
    )
    from backend.utils.sandbox import (
        close_sandbox_runner,
        initialize_sandbox_runner,
    )
    from backend.utils.scholarly_research import (
        close_research_http_client,
        initialize_research_http_client,
    )
    from backend.utils.web_search import (
        close_deep_reader,
        close_search_manager,
        close_structured_page_reader,
        initialize_structured_page_reader,
    )
    from backend.modules.bib_manager.provider_manager import (
        close_provider_manager,
    )

    # 沙箱运行时预热：探测 Docker daemon 可达性（auto 模式据此决定首请求走向）
    try:
        await initialize_sandbox_runner()
    except Exception as e:
        logger.warning("Sandbox runtime initialization failed: %s", e)

    await initialize_research_http_client()
    browser_health = await initialize_structured_page_reader()
    if browser_health.enabled and not browser_health.ready:
        logger.warning(
            "Browser page reading is enabled but unavailable: %s",
            browser_health.last_error,
        )
    cleanup_task = asyncio.create_task(cleanup_loop())
    plot_cleanup_task = asyncio.create_task(plot_cleanup_loop())
    style_modify_cleanup_task = asyncio.create_task(style_modify_cleanup_loop())
    mu_maintenance_task = None
    mail_outbox_task = None
    if get_config().multi_user.enabled:
        from backend.core.multiuser.maintenance import maintenance_loop
        from backend.core.multiuser.mail import outbox_loop

        mu_maintenance_task = asyncio.create_task(maintenance_loop())
        # SMTP 邮件发送协程（v2 P6）：消费 email_outbox 队列；未启用 SMTP 时
        # 循环空转零开销（process_outbox 前置就绪判断直接返回）
        mail_outbox_task = asyncio.create_task(outbox_loop())
    try:
        yield
    finally:
        cleanup_task.cancel()
        plot_cleanup_task.cancel()
        style_modify_cleanup_task.cancel()
        if mu_maintenance_task is not None:
            mu_maintenance_task.cancel()
        if mail_outbox_task is not None:
            mail_outbox_task.cancel()
        try:
            await cleanup_task
        except asyncio.CancelledError:
            pass
        try:
            await plot_cleanup_task
        except asyncio.CancelledError:
            pass
        try:
            await style_modify_cleanup_task
        except asyncio.CancelledError:
            pass
        if mu_maintenance_task is not None:
            try:
                await mu_maintenance_task
            except asyncio.CancelledError:
                pass
        if mail_outbox_task is not None:
            try:
                await mail_outbox_task
            except asyncio.CancelledError:
                pass
        try:
            await close_search_manager()
        except Exception as e:
            logger.warning("Failed to close search manager: %s", e)
        try:
            await close_deep_reader()
        except Exception as e:
            logger.warning("Failed to close deep reader: %s", e)
        try:
            await close_structured_page_reader()
        except Exception as e:
            logger.warning("Failed to close structured page reader: %s", e)
        try:
            await close_provider_manager()
        except Exception as e:
            logger.warning("Failed to close bib providers: %s", e)
        try:
            await close_sandbox_runner()
        except Exception as e:
            logger.warning("Failed to close sandbox runner: %s", e)
        await close_research_http_client()
        # 应用关停时清理 LLM 客户端池（释放各档案 AsyncOpenAI 底层连接）
        from backend.core.model_router import reset_client_pool

        reset_client_pool()
        logger.info("Shutting down LaTeXcellence server")


app = FastAPI(
    title="LaTeXcellence",
    description="让技术退居幕后，让思想落于纸上",
    version="2.1.0",
    lifespan=lifespan,
)

# CORS + 访问令牌中间件：白名单来自 server.allowed_origins，
# 令牌校验来自 security.access_token，均每次请求实时读取（支持热重载）。
# 多用户模式会话验证成功时，中间件同时注入请求级当前用户上下文
# （LLM 治理 / 会话类资源归属依赖）。
app.add_middleware(AccessSecurityMiddleware)

# LLM 每用户治理错误 → HTTP 429 + Retry-After（v2 P1b，注册逻辑见
# llm_governance.register_governance_handler；普通 RuntimeError 原样重抛）
register_governance_handler(app)


@app.get("/api/health")
async def health_check():
    """
    健康检查端点。
    返回服务器运行状态、版本号和当前使用的模型名称。
    """
    cfg = get_config()
    return {
        "status": "ok",
        "version": "2.1.0",
        "model": cfg.llm.model,
    }


@app.get("/api/config/status")
async def config_status():
    """
    配置状态查询端点。
    返回 LLM 配置概览和各项功能的启用状态，便于前端初始化时判断能力范围。
    """
    cfg = get_config()
    has_api_key = bool(cfg.llm.api_key)
    return {
        "model": cfg.llm.model,
        "has_api_key": has_api_key,  # 前端据此决定是否显示 LLM 功能入口
        "dev_mode": cfg.server.dev_mode,
        "multi_user": cfg.multi_user.enabled,  # 多用户模式开关（登录页据此判断模式）
        # 是否开放注册（登录/注册页据此隐藏注册入口，避免点了才被 400 拒绝）
        "registration_enabled": cfg.multi_user.registration_enabled,
        # 多用户上传上限（前端目录直传据此做浏览器侧前置校验，
        # 避免打包上传整个目录后才被 413 拒绝；只读透出既有配置，非新增配置项）
        "max_upload_files": cfg.multi_user.max_upload_files,
        "max_upload_file_size_mb": cfg.multi_user.max_upload_file_size_mb,
        # 分片上传切片大小（v2 P5）：前端按该值切片以对齐后端 PUT 校验上限
        "chunk_part_max_mb": cfg.multi_user.chunk_part_max_mb,
        # SMTP 邮件能力（v2 P6）：登录页据此隐藏/展示「忘记密码」入口
        "email_available": bool(cfg.smtp.enabled and cfg.smtp.host.strip()),
        "features": {
            name: getattr(cfg.features, name).enabled
            for name in [
                "typo_check",
                "expression_opt",
                "formula_ocr",
                "table_ocr",
                "style_modify",
                "template_gen",
                "template_library",
                "code_review",
                "compile",
                "git_diff",
                "git_manage",
                "format_converter",
                "terminology_check",
                "paragraph_translate",
                "writing_chat",
                "bib_manager",
                "plot_gen",
                "editor",
            ]
        }
        | {
            "web_search": cfg.features.function_calling.web_search.enabled,
            "project_tools": cfg.features.function_calling.project_tools.enabled,
            "scholarly_research": cfg.features.function_calling.scholarly_research.enabled,
            # 编辑器侧 AI 助手独立子开关（决策 Q26）：前端据此隐藏 AI Tab
            "editor_assistant": cfg.features.editor.assistant_enabled,
        },
    }


@app.get("/api/config")
async def get_full_config(
    _: None = Depends(require_admin_if_multiuser),
):
    """
    获取完整配置（api_key 脱敏处理）。

    返回所有配置项供前端设置页面使用。LLM api_key 仅展示后 4 位，
    前端以掩码形式（***xxxx）显示。
    多用户模式下仅管理员可访问（单用户模式保持原行为）。
    """
    cfg = get_config()
    data = cfg.model_dump()
    key = data["llm"]["api_key"]
    if key:
        data["llm"]["api_key"] = "***" + key[-4:] if len(key) > 4 else "****"
    # 模型档案 api_key 与顶层 Key 同口径脱敏：仅展示后 4 位，防止明文泄露
    for profile in data["llm"].get("profiles", []):
        profile_key = profile.get("api_key", "")
        if profile_key:
            profile["api_key"] = (
                "***" + profile_key[-4:] if len(profile_key) > 4 else "****"
            )
    providers = data["features"]["function_calling"]["scholarly_research"]["providers"]
    for provider_key in ("openalex", "semantic_scholar", "core", "unpaywall", "pubmed"):
        p = providers.get(provider_key, {})
        if "api_key" not in p:
            continue
        k = p["api_key"]
        if k:
            p["api_key"] = "***" + k[-4:] if len(k) > 4 else "****"
    tavily = (
        data.get("features", {})
        .get("function_calling", {})
        .get("web_search", {})
        .get("engines", {})
        .get("tavily", {})
    )
    if isinstance(tavily, dict) and "api_key" in tavily:
        k = tavily["api_key"]
        if k:
            tavily["api_key"] = "***" + k[-4:] if len(k) > 4 else "****"
    # 访问令牌只展示后 4 位，防止通过配置接口泄露完整令牌
    token = data.get("security", {}).get("access_token", "")
    if token:
        data["security"]["access_token"] = (
            "***" + token[-4:] if len(token) > 4 else "****"
        )
    # SMTP 密码（v2 P6）同口径脱敏：仅展示后 4 位
    smtp_password = data.get("smtp", {}).get("password", "")
    if smtp_password:
        data["smtp"]["password"] = (
            "***" + smtp_password[-4:] if len(smtp_password) > 4 else "****"
        )
    return data


@app.post("/api/config")
async def update_config(
    payload: dict,
    _: None = Depends(require_admin_if_multiuser),
):
    """
    更新配置并持久化到 config.json。
    支持部分更新——只需传入需要修改的字段即可。
    多用户模式下仅管理员可访问（单用户模式保持原行为）。
    """
    try:
        update_config_from_dict(payload)
    except ValidationError as exc:
        details = "; ".join(
            f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
            for error in exc.errors()
        )
        get_logger("main").warning("Config validation failed: %s", details)
        raise HTTPException(
            status_code=400, detail=f"Config validation failed: {details}"
        )
    return {"status": "ok"}


@app.get("/api/llm/routes")
async def get_llm_routes(_: None = Depends(require_login_if_multiuser)):
    """
    查询各角色槽位当前生效的模型档案解析结果（multi-model-plan §5.4）。

    固定覆盖全部角色槽位，每项为该角色经两级解析（指派槽 → default 槽 →
    内置档案）得到的档案展示信息，不含 api_key / base_url 等敏感字段；
    每次请求实时解析，不缓存（热重载语义）。

    权限（multi-model-plan §7.2 裁决）：模型为服务端全局配置、对用户只读，
    功能页模型徽标在多用户模式下对所有登录用户展示，故本端点为"多用户
    模式已登录即可读"（require_login_if_multiuser），宽于 /api/config 的
    管理员门禁（响应无脱敏密钥，泄露面仅模型名）；单用户模式放行，仍受
    共享访问令牌中间件约束。
    """
    return {"routes": resolve_role_routes()}


# ── 模型连通性测试（deploy-followups-plan §4.4，阶段四）──────────────────

# 诊断补全的超时上限（秒）：一次最小补全（max_tokens=1）的往返硬上限
_LLM_TEST_TIMEOUT_SECONDS = 15.0


class LlmTestRequest(BaseModel):
    """POST /api/llm/test 请求体：profile_id 与 role 二选一。

    掩码协议边界（Q9）：前端只传目标标识，api_key 等敏感材料一律由后端
    直接读服务端配置真实值，严禁前端回传。
    """

    profile_id: str | None = None
    role: str | None = None


def _llm_test_error(code: str, message: str, **extra: Any) -> dict[str, Any]:
    """构造测试结果内联错误对象。

    键名与 HTTPException detail 的结构化错误约定一致（{"error": 码,
    "message": 文案, ...附加字段}），前端 translateError() 可直接消费；
    脱敏：不含密钥与上游响应体。
    """
    error: dict[str, Any] = {"error": code, "message": message}
    error.update(extra)
    return error


def _classify_llm_test_error(exc: Exception) -> dict[str, Any]:
    """把连通性测试异常归类为机器可读错误码 + 有限脱敏信息。

    仅保留错误类别与上游 HTTP 状态码两类安全字段，严禁回显 api_key 或
    上游响应体（可能含请求回显）；完整异常细节只进服务端日志。
    注意 APITimeoutError 是 APIConnectionError 的子类，必须先判超时。
    """
    if isinstance(exc, (APITimeoutError, TimeoutError)):
        return _llm_test_error(
            "llm_test_timeout",
            "Connectivity test timed out; check the network and base_url",
        )
    if isinstance(exc, APIStatusError):
        status = getattr(exc, "status_code", None)
        return _llm_test_error(
            "llm_test_upstream",
            f"Upstream API returned HTTP {status}",
            status=status,
        )
    if isinstance(exc, APIConnectionError):
        return _llm_test_error(
            "llm_test_unreachable",
            "Failed to reach the API endpoint; check the network and base_url",
        )
    return _llm_test_error(
        "llm_test_failed", "Connectivity test failed; check the profile settings"
    )


@app.post("/api/llm/test")
async def test_llm_connectivity(
    payload: LlmTestRequest,
    _: None = Depends(require_admin_if_multiuser),
):
    """模型连通性测试：向目标档案发起一次最小补全并计测往返延迟。

    双粒度（二选一，均为 422 语义错误拒绝）：
        - role：经 get_client_for(role) 两级解析（指派槽 → default 槽 →
          内置档案），验证"该槽位实际生效的档案"；
        - profile_id：直取该档案本身（get_client_for_profile），验证
          "档案当前配置是否可连通"。

    执行语义：LLMClient.chat() 最小补全（"ping"、max_tokens=1、
    max_retries=1 快速失败），asyncio.timeout 15s 硬上限；测试结果
    （含失败）一律以 200 + ok=false + error{} 内联返回，只有权限 /
    参数 / 目标不存在走 HTTP 错误状态码。

    掩码协议边界（Q9）：后端直接读服务端配置真实值，前端只传
    profile_id；本端点只读配置，绝不落盘 / 变更任何配置项。

    多用户 LLM 治理豁免（有意偏离，deploy-followups-plan §4.4）：触发
    真实计费的诊断操作属管理面（require_admin_if_multiuser），不应
    消耗管理员的个人速率窗口 / 并发租约，故 chat 调用包裹在
    admission_exempt_scope() 内——详见 llm_governance 模块注释。
    """
    if bool(payload.profile_id) == bool(payload.role):
        raise HTTPException(
            status_code=422,
            detail={
                "error": "invalid_request",
                "message": "Provide exactly one of profile_id or role",
            },
        )

    role: str | None = None
    if payload.role is not None:
        if payload.role not in LLM_ROLE_KEYS:
            raise HTTPException(
                status_code=422,
                detail={
                    "error": "invalid_request",
                    "message": f"Unknown role: {payload.role}",
                },
            )
        role = payload.role
        profile = get_config().llm.resolve_role(role)
        client = get_client_for(role)
    else:
        try:
            profile = resolve_profile_by_id(payload.profile_id or "")
            client = get_client_for_profile(payload.profile_id or "")
        except ValueError as exc:
            raise HTTPException(
                status_code=404,
                detail={"error": "not_found", "message": str(exc)},
            ) from None

    logger = get_logger("main")

    def _response(ok: bool, latency_ms: int, **extra: Any) -> dict[str, Any]:
        """组装响应体：model / profile_id 来自解析后的档案（role 粒度即
        两级解析结果），不含 api_key / base_url 等敏感字段。"""
        body: dict[str, Any] = {
            "ok": ok,
            "latency_ms": latency_ms,
            "model": profile.model,
            "profile_id": profile.id,
        }
        if role is not None:
            body["role"] = role
        body.update(extra)
        return body

    if not profile.api_key:
        logger.info(
            "LLM connectivity test skipped (no api key): profile=%s", profile.id
        )
        return _response(
            ok=False,
            latency_ms=0,
            error=_llm_test_error(
                "api_key_missing",
                "No API key configured for this profile",
            ),
        )

    logger.info(
        "LLM connectivity test started: profile=%s role=%s model=%s",
        profile.id,
        role or "-",
        profile.model,
    )
    started = time.perf_counter()
    try:
        # 治理豁免仅包裹诊断调用本身（见 docstring 与
        # llm_governance.admission_exempt_scope 注释）
        async with admission_exempt_scope():
            async with asyncio.timeout(_LLM_TEST_TIMEOUT_SECONDS):
                await client.chat(
                    messages=[{"role": "user", "content": "ping"}],
                    max_tokens=1,
                    max_retries=1,
                )
    except Exception as exc:
        latency_ms = int((time.perf_counter() - started) * 1000)
        error = _classify_llm_test_error(exc)
        # 服务端日志只记档案标识与错误类别，绝不记录异常细节 / 密钥 /
        # 上游响应内容
        logger.warning(
            "LLM connectivity test failed: profile=%s category=%s latency_ms=%d",
            profile.id,
            error["error"],
            latency_ms,
        )
        return _response(ok=False, latency_ms=latency_ms, error=error)

    latency_ms = int((time.perf_counter() - started) * 1000)
    logger.info(
        "LLM connectivity test ok: profile=%s latency_ms=%d",
        profile.id,
        latency_ms,
    )
    return _response(ok=True, latency_ms=latency_ms)


# 注册各功能模块路由
app.include_router(project_router)
app.include_router(typo_check_router)
app.include_router(expression_opt_router)
app.include_router(template_gen_router)
# 模板库路由（内置模板 + 用户模板库；features.template_library.enabled=false 时端点关闭）
app.include_router(template_library_router)
app.include_router(formula_ocr_router)
app.include_router(table_ocr_router)
app.include_router(code_review_router)
app.include_router(compile_router)
app.include_router(git_diff_router)
app.include_router(git_manage_router)
app.include_router(format_converter_router)
app.include_router(style_modify_router)
app.include_router(terminology_check_router)
app.include_router(paragraph_translate_router)
app.include_router(search_router)
app.include_router(writing_chat_router)
app.include_router(bib_manager_router)
app.include_router(plot_gen_router)
# 多用户模式认证与管理员路由（未开启模式时各端点返回 404）
app.include_router(auth_router)
app.include_router(auth_admin_router)
# 多用户模式项目路由（上传 / 下载 / 回收站 / 配额；未开启模式时 404）
app.include_router(projects_router)


def main():
    """
    主函数：加载配置、初始化日志、按模式挂载前端、启动 Uvicorn 服务器。
    命令行参数可指定配置文件路径，默认为 "config.json"。
    """
    config_path = sys.argv[1] if len(sys.argv) > 1 else "config.json"

    # 加载配置（Pydantic 校验自动执行）
    load_config(config_path)
    cfg = get_config()

    # 初始化日志系统（按日期旋转，自动写入 logs/ 目录）
    setup_logging(level=cfg.logging.level, log_dir=cfg.logging.dir)
    logger = get_logger("main")
    logger.info(f"Config loaded from {config_path}")

    # 检查 API Key 是否配置，未配置时发出警告但允许启动
    has_api_key = bool(cfg.llm.api_key)
    if not has_api_key:
        logger.warning("No API key configured — LLM features will not work")

    # 按模式挂载前端：
    #   开发模式 → 期望 Vite dev server 独立运行，后端只提供 API
    #   生产模式 → 将 Vite 构建产物（static/）挂载为带 SPA 回退的静态服务：
    #     客户端路由（如 /formula-ocr）刷新 / 直达时物理文件不存在，
    #     SPAStaticFiles 对期望 HTML 的页面导航回退 index.html，由前端路由接管
    if cfg.server.dev_mode:
        logger.info(
            f"Dev mode enabled — expecting Vite dev server at {cfg.server.vite_dev_url}"
        )
    else:
        static_dir = Path("static")
        if static_dir.exists() and any(static_dir.iterdir()):
            app.mount("/", SPAStaticFiles(directory="static", html=True), name="static")
            logger.info("Mounted static files from static/ (with SPA fallback)")
        else:
            logger.warning(
                "Production mode but static/ is empty — run 'npm run build' in frontend/"
            )

    # 启动 Uvicorn HTTP 服务器
    uvicorn.run(
        app,
        host=cfg.server.host,
        port=cfg.server.port,
        log_level=cfg.logging.level.lower(),
    )


if __name__ == "__main__":
    main()
