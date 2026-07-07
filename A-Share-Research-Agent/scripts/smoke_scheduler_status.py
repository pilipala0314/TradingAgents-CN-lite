"""一次性端到端 smoke: 验证 /admin/scheduler/status 返回结构。"""
import asyncio
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from httpx import ASGITransport, AsyncClient

from services.scheduler_status_service import set_scheduler


async def main():
    from apps.api.main import app

    with patch("apps.scheduler.etl_scheduler.start_scheduler", return_value=MagicMock()):
        mock = MagicMock()
        mock.timezone = "Asia/Shanghai"
        now = datetime.now(timezone.utc)
        j1 = MagicMock()
        j1.id = "daily_close_etl"
        j1.name = "_safe_dispatch_yesterday"
        j1.trigger = "cron[day_of_week='mon-fri', hour='17', minute='0']"
        j1.next_run_time = now + timedelta(hours=20, minutes=33)
        j1.coalesce = True
        j1.max_instances = 1
        j1.func = lambda: None
        j1.func_ref = None
        j2 = MagicMock()
        j2.id = "daily_research_flow"
        j2.name = "_safe_run_research"
        j2.trigger = "cron[day_of_week='mon-fri', hour='17', minute='30']"
        j2.next_run_time = now + timedelta(hours=21, minutes=3)
        j2.coalesce = True
        j2.max_instances = 1
        j2.func = lambda: None
        j2.func_ref = None
        mock.get_jobs.return_value = [j1, j2]
        set_scheduler(mock)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            r = await c.get("/admin/scheduler/status")
            print("STATUS", r.status_code)
            print(json.dumps(r.json(), indent=2, ensure_ascii=False))


asyncio.run(main())
