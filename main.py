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
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import uvicorn

from backend.core.config import load_config, get_config, update_config_from_dict
from backend.core.logger import setup_logging, get_logger
from backend.modules.project_router import router as project_router
from backend.modules.typo_check.router import router as typo_check_router
from backend.modules.expression_opt.router import router as expression_opt_router
from backend.modules.template_gen.router import router as template_gen_router
from backend.modules.formula_ocr.router import router as formula_ocr_router
from backend.modules.table_ocr.router import router as table_ocr_router
from backend.modules.code_review.router import router as code_review_router
from backend.modules.compile.router import router as compile_router
from backend.modules.git_diff.router import router as git_diff_router
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

    from backend.modules.template_gen.cleanup import cleanup_loop

    cleanup_task = asyncio.create_task(cleanup_loop())

    yield

    cleanup_task.cancel()
    try:
        await cleanup_task
    except asyncio.CancelledError:
        pass
    logger.info("Shutting down LaTeXcellence server")


app = FastAPI(
    title="LaTeXcellence",
    description="让排版隐于无形，让思考回归本身",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS 中间件：允许所有来源的跨域请求
# 开发模式下前端（localhost:5173）和后端（localhost:8000）不同源，需要此配置
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
async def health_check():
    """
    健康检查端点。
    返回服务器运行状态、版本号和当前使用的模型名称。
    """
    cfg = get_config()
    return {
        "status": "ok",
        "version": "0.1.0",
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
        "features": {
            name: getattr(cfg.features, name).enabled
            for name in [
                "typo_check",
                "expression_opt",
                "formula_ocr",
                "table_ocr",
                "style_modify",
                "template_gen",
                "code_review",
                "compile",
                "git_diff",
                "format_converter",
                "terminology_check",
                "paragraph_translate",
                "writing_chat",
                "bib_manager",
            ]
        }
        | {
            "web_search": cfg.features.function_calling.web_search.enabled,
            "project_tools": cfg.features.function_calling.project_tools.enabled,
        },
    }


@app.get("/api/config")
async def get_full_config():
    """
    获取完整配置（api_key 脱敏处理）。

    返回所有配置项供前端设置页面使用。LLM api_key 仅展示后 4 位，
    前端以掩码形式（***xxxx）显示。
    """
    cfg = get_config()
    data = cfg.model_dump()
    key = data["llm"]["api_key"]
    if key:
        data["llm"]["api_key"] = "***" + key[-4:] if len(key) > 4 else "****"
    return data


@app.post("/api/config")
async def update_config(payload: dict):
    """
    更新配置并持久化到 config.json。
    支持部分更新——只需传入需要修改的字段即可。
    """
    update_config_from_dict(payload)
    return {"status": "ok"}


# 注册各功能模块路由
app.include_router(project_router)
app.include_router(typo_check_router)
app.include_router(expression_opt_router)
app.include_router(template_gen_router)
app.include_router(formula_ocr_router)
app.include_router(table_ocr_router)
app.include_router(code_review_router)
app.include_router(compile_router)
app.include_router(git_diff_router)
app.include_router(format_converter_router)
app.include_router(style_modify_router)
app.include_router(terminology_check_router)
app.include_router(paragraph_translate_router)
app.include_router(search_router)
app.include_router(writing_chat_router)
app.include_router(bib_manager_router)


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
    #   生产模式 → 将 Vite 构建产物（static/）作为静态文件挂载
    if cfg.server.dev_mode:
        logger.info(
            f"Dev mode enabled — expecting Vite dev server at {cfg.server.vite_dev_url}"
        )
    else:
        static_dir = Path("static")
        if static_dir.exists() and any(static_dir.iterdir()):
            app.mount("/", StaticFiles(directory="static", html=True), name="static")
            logger.info("Mounted static files from static/")
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
