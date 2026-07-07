"""apps.api.routers — API 端点按域拆分。

- health.py    /health
- etl.py       /v1/etl/*
- rules.py     /v1/rules/*
- research.py  /v1/research/*
- macro.py     /v1/macro/*  （P0 补强）

每个文件只暴露一个 `router = APIRouter(...)`，在 `main.py` 中统一 `include_router`。
"""
