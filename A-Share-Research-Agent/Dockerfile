FROM python:3.10-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# 系统级依赖：libpq5 用于 asyncpg，ca-certificates 用于 HTTPS
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq5 \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 先装依赖，再拷代码，这样改代码不用重装依赖
COPY pyproject.toml README.md ./
RUN pip install --no-cache-dir \
        "sqlalchemy>=2.0.0" \
        "asyncpg>=0.29.0" \
        "fastapi>=0.110.0" \
        "uvicorn>=0.27.0" \
        "langgraph>=0.0.26" \
        "langchain-openai>=0.0.8" \
        "pydantic>=2.6.0" \
        "pydantic-settings>=2.2.0" \
        "akshare>=1.13.0" \
        "pandas>=2.2.0" \
        "alembic>=1.13.0" \
        "httpx>=0.25.0,<0.26.0" \
        "requests>=2.31.0" \
        "mootdx>=0.11.7" \
        "celery>=5.3.0" \
        "redis>=5.0.0" \
        "flower>=2.0.0" \
        "apscheduler>=3.10.0" \
    || (echo "[build] retry with system index..." && \
        pip install --no-cache-dir --index-url https://pypi.org/simple \
        "sqlalchemy>=2.0.0" "asyncpg>=0.29.0" "fastapi>=0.110.0" \
        "uvicorn>=0.27.0" "langgraph>=0.0.26" "langchain-openai>=0.0.8" \
        "pydantic>=2.6.0" "pydantic-settings>=2.2.0" "akshare>=1.13.0" \
        "pandas>=2.2.0" "alembic>=1.13.0" "httpx>=0.25.0,<0.26.0" \
        "requests>=2.31.0" "mootdx>=0.11.7" \
        "celery>=5.3.0" "redis>=5.0.0" "flower>=2.0.0" "apscheduler>=3.10.0")

# 拷贝项目代码（本地多包平铺结构）
COPY . /app

EXPOSE 8765
CMD ["uvicorn", "apps.api.main:app", "--host", "0.0.0.0", "--port", "8765"]
