# Argus 后端：REST /api + MCP /mcp；依赖统一用 uv 按 uv.lock 安装，启动前先执行数据库迁移。
FROM python:3.12-slim

# 默认走阿里云 PyPI 镜像（服务器在国内）；其他网络环境可 --build-arg PYPI_INDEX=... 覆盖。
ARG PYPI_INDEX=https://mirrors.aliyun.com/pypi/simple/

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_INDEX_URL=${PYPI_INDEX} \
    UV_DEFAULT_INDEX=${PYPI_INDEX}

WORKDIR /app
COPY pyproject.toml uv.lock alembic.ini ./
COPY app ./app
COPY migrations ./migrations

RUN pip install --no-cache-dir uv==0.6.6 \
 && uv sync --frozen --no-dev

ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8000
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port 8000"]
