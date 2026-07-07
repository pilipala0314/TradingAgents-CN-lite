.PHONY: help install lint test test-fast docker-up docker-down docker-logs docker-rebuild api-shell db-shell alembic-up alembic-down alembic-revision etl-close research-live research-report worker-up worker-flower clean

# 默认目标
help:
	@echo "A-Share-Research-Agent Makefile"
	@echo ""
	@echo "基础:"
	@echo "  install         装依赖 (pip install -r requirements.txt)"
	@echo "  test            跑全部 pytest (71 测试)"
	@echo "  test-fast       只跑快测试 (排除 e2e)"
	@echo "  lint            ruff check (需装 ruff)"
	@echo ""
	@echo "Docker (5 服务: postgres + redis + api + worker + flower):"
	@echo "  docker-up       docker compose up -d --build (首次部署)"
	@echo "  docker-down     docker compose down (不丢数据卷)"
	@echo "  docker-logs     跟踪所有服务日志"
	@echo "  docker-rebuild  重新构建 + 重启指定服务 (SVC=api)"
	@echo "  api-shell       容器内 bash"
	@echo "  db-shell        psql 进数据库"
	@echo ""
	@echo "Alembic 迁移:"
	@echo "  alembic-up      升级到 head"
	@echo "  alembic-down    降一级"
	@echo "  alembic-revision MSG='...'  新建迁移"
	@echo ""
	@echo "业务调用:"
	@echo "  etl-close DATE=2026-07-01 SYMS=000636  跑收盘 ETL"
	@echo "  research-live SYM=000636                 拉个股实景 (a-stock-data 实时)"
	@echo "  research-report DATE=2026-07-01          触发报告渲染"
	@echo ""
	@echo "Celery 调试:"
	@echo "  worker-up       celery worker (前台, Ctrl+C 退出)"
	@echo "  worker-flower   flower UI (localhost:5555)"

# ── 基础 ──
install:
	pip install -r ../requirements.txt || true
	pip install celery redis flower apscheduler pytest-asyncio httpx 'httpx<0.26.0'

test:
	py -3.10 -m pytest -v

test-fast:
	py -3.10 -m pytest -q --ignore=tests/test_graph_e2e.py

lint:
	ruff check apps/ data_pipeline/ agents/ graph/ core/ 2>/dev/null || echo "(ruff 未安装,跳过)"

# ── Docker ──
docker-up:
	docker compose up -d --build

docker-down:
	docker compose down

docker-logs:
	docker compose logs -f --tail=100

docker-rebuild:
ifdef SVC
	docker compose up -d --build $(SVC)
else
	@echo "用法: make docker-rebuild SVC=api"
endif

api-shell:
	docker exec -it ashare-api bash

db-shell:
	docker exec -it ashare-postgres psql -U $$POSTGRES_USER -d $$POSTGRES_DB

# ── Alembic ──
alembic-up:
	docker exec -it ashare-api alembic upgrade head

alembic-down:
	docker exec -it ashare-api alembic downgrade -1

alembic-revision:
	docker exec -it ashare-api alembic revision --autogenerate -m "$(MSG)"

# ── 业务 ──
etl-close:
ifndef DATE
	@echo "用法: make etl-close DATE=2026-07-01 [SYMS=000636,600519]"
	@exit 1
endif
ifdef SYMS
	@powershell -Command "$$body = @{business_date='$(DATE)'; symbols=@($(SYMS:','=',',))) | ConvertTo-Json -Depth 5; Invoke-RestMethod -Uri 'http://localhost:8765/v1/etl/close' -Method Post -Body $$body -ContentType 'application/json' -TimeoutSec 180"
else
	@powershell -Command "$$body = @{business_date='$(DATE)'} | ConvertTo-Json -Depth 5; Invoke-RestMethod -Uri 'http://localhost:8765/v1/etl/close' -Method Post -Body $$body -ContentType 'application/json' -TimeoutSec 180"
endif

research-live:
ifndef SYM
	@echo "用法: make research-live SYM=000636"
	@exit 1
endif
	@powershell -Command "$$body = @{symbol='$(SYM)'} | ConvertTo-Json -Depth 3; $$r = Invoke-RestMethod -Uri 'http://localhost:8765/v1/research/analyze_stock_live' -Method Post -Body $$body -ContentType 'application/json' -TimeoutSec 30; $$r.result | ConvertTo-Json -Depth 8"

research-report:
ifndef DATE
	@echo "用法: make research-report DATE=2026-07-01"
	@exit 1
endif
	@powershell -Command "$$body = @{business_date='$(DATE)'; market_snapshot=@{market_regime='risk_on_expansion'}; market_rule_hits=@()} | ConvertTo-Json -Depth 8; Invoke-RestMethod -Uri 'http://localhost:8765/v1/research/report' -Method Post -Body $$body -ContentType 'application/json' -TimeoutSec 60 | Select-Object -ExpandProperty report_markdown"

# ── Celery ──
worker-up:
	docker exec -it ashare-worker celery -A apps.worker.celery_app.celery_app worker --loglevel=INFO --concurrency=2

worker-flower:
	docker exec -it ashare-flower celery -A apps.worker.celery_app.celery_app flower --port=5555

# ── 清理 ──
clean:
	@find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	@find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	@find . -type d -name .ruff_cache -exec rm -rf {} + 2>/dev/null || true
	@find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
	@echo "清理完成"
