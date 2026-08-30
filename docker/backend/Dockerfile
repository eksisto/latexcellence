# ─────────────────────────────────────────────────────────────
# LaTeXcellence 应用本体镜像（backend）
# 基础镜像不写死架构：amd64 / arm64 均可原生构建
#
# 构建示例：
#   docker build -t latexcellence-backend:latest docker/backend
#   # 可选国内 pip 源：
#   docker build --build-arg PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple \
#                -t latexcellence-backend:latest docker/backend
#
# 运行约定（config.json 与 data/ 由运行时挂载，凭证不打进镜像）：
#   docker run -v ./config.json:/app/config.json:ro \
#              -v ./data:/app/data \
#              -p 8000:8000 latexcellence-backend:latest
# ─────────────────────────────────────────────────────────────
FROM python:3.12-slim

# 非 root 运行身份，UID/GID 可通过 build-arg 与宿主后端账号对齐（默认 1000）
ARG UID=1000
ARG GID=1000
# 可选覆盖 pip 源；留空使用 PyPI 官方默认（${VAR:+...} 为空时不追加参数）
ARG PIP_INDEX_URL=""

LABEL org.opencontainers.image.title="LaTeXcellence Backend" \
      org.opencontainers.image.source="https://github.com/eksisto/latexcellence.git" \
      org.opencontainers.image.description="LaTeXcellence 应用本体：FastAPI 后端 + Vite 构建产物；含 unar（rar 解压）与 git"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# 依赖清单先复制，最大化利用层缓存；apt 与 pip 合并同一层并在同层清理缓存
COPY requirements.txt .
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
        git \
        unar; \
    rm -rf /var/lib/apt/lists/*; \
    pip install --no-cache-dir ${PIP_INDEX_URL:+--index-url "$PIP_INDEX_URL"} -r requirements.txt; \
    groupadd --gid "$GID" latexcellence; \
    useradd --uid "$UID" --gid latexcellence --create-home --shell /bin/bash latexcellence

# 应用源码与前端构建产物（config.json / data/ 不打入镜像，运行时挂载注入）
# 说明：playwright 仅安装 Python 包，浏览器内核不随镜像分发；
#       web_search 的 Playwright 回退池在容器内不可用时会自动走 HTTP 抓取路径。
COPY --chown=$UID:$GID main.py ./
COPY --chown=$UID:$GID backend ./backend
COPY --chown=$UID:$GID static ./static

USER latexcellence

EXPOSE 8000

CMD ["python", "main.py", "config.json"]
