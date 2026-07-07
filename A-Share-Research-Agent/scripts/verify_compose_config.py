"""docker-compose 5 服务静态检查。

不依赖 docker daemon；纯配置 + healthcheck 字段完整性检查。

校验项：
- 5 个服务都在（postgres / redis / api / worker / flower）
- 关键 healthcheck 字段（test / interval / timeout / retries）
- depends_on 链路（api 依赖 postgres/redis，worker 同，flower 依赖 redis）
- ports 绑定（API_PORT / REDIS_PORT / POSTGRES_PORT / FLOWER_PORT）
- volumes（postgres 持久化）
"""
from __future__ import annotations

import sys
from pathlib import Path

import yaml

REQUIRED_SERVICES = {"postgres", "redis", "api", "worker", "flower"}
REQUIRED_HEALTHCHECK_KEYS = {"test", "interval", "timeout", "retries"}


def _check_compose(path: Path) -> tuple[int, int, list[str]]:
    """返 (err_count, warn_count, messages)。"""
    msgs: list[str] = []
    err = 0
    warn = 0

    if not path.exists():
        return 1, 0, [f"❌ 缺文件: {path}"]

    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        return 1, 0, [f"❌ YAML 解析失败: {e}"]

    services = data.get("services") or {}
    msgs.append(f"  services: {sorted(services.keys())}")

    missing = REQUIRED_SERVICES - set(services.keys())
    if missing:
        msgs.append(f"❌ 缺服务: {sorted(missing)}")
        err += 1
    else:
        msgs.append(f"✅ 5 服务齐: {sorted(REQUIRED_SERVICES)}")

    # healthcheck 完整性
    for name, svc in services.items():
        hc = svc.get("healthcheck")
        if hc is None:
            msgs.append(f"⚠ {name}: 缺 healthcheck")
            warn += 1
            continue
        missing_keys = REQUIRED_HEALTHCHECK_KEYS - set(hc.keys())
        if missing_keys:
            msgs.append(f"❌ {name}: healthcheck 缺字段 {sorted(missing_keys)}")
            err += 1
        else:
            msgs.append(f"✅ {name}: healthcheck 完整")

    # depends_on 链路
    api_deps = set((services.get("api") or {}).get("depends_on") or {})
    if {"postgres", "redis"} - api_deps:
        msgs.append(f"❌ api: depends_on 缺 {sorted({'postgres', 'redis' - api_deps})}")
        err += 1
    else:
        msgs.append("✅ api depends_on: postgres + redis")
    worker_deps = set((services.get("worker") or {}).get("depends_on") or {})
    if {"postgres", "redis"} - worker_deps:
        msgs.append(f"❌ worker: depends_on 缺 {sorted({'postgres', 'redis' - worker_deps})}")
        err += 1
    else:
        msgs.append("✅ worker depends_on: postgres + redis")
    flower_deps = set((services.get("flower") or {}).get("depends_on") or {})
    if "redis" not in flower_deps:
        msgs.append("❌ flower: depends_on 缺 redis")
        err += 1
    else:
        msgs.append("✅ flower depends_on: redis")

    # ports
    for name, port_var in [("postgres", "POSTGRES_PORT"), ("redis", "REDIS_PORT"),
                            ("api", "API_PORT"), ("flower", "FLOWER_PORT")]:
        ports = (services.get(name) or {}).get("ports") or []
        if not ports:
            msgs.append(f"❌ {name}: 缺 ports")
            err += 1
        else:
            msgs.append(f"✅ {name}: ports={ports}")

    # volumes
    if "volumes" not in data:
        msgs.append("⚠ 顶层缺 volumes 段（postgres 持久化需要）")
        warn += 1
    else:
        msgs.append("✅ volumes 段存在")

    # worker command 检查
    worker_cmd = (services.get("worker") or {}).get("command")
    if worker_cmd and "celery" in (worker_cmd if isinstance(worker_cmd, str) else " ".join(worker_cmd)):
        msgs.append("✅ worker command: celery worker")
    else:
        msgs.append(f"⚠ worker command: {worker_cmd!r}（未发现 celery worker 关键字）")
        warn += 1

    return err, warn, msgs


def main() -> int:
    compose_path = Path(__file__).resolve().parent.parent / "docker-compose.yml"
    print("=" * 60)
    print(f"docker-compose 静态检查: {compose_path}")
    print("=" * 60)
    err, warn, msgs = _check_compose(compose_path)
    for m in msgs:
        print(m)
    print("=" * 60)
    if err:
        print(f"❌ {err} errors, {warn} warnings")
        return 1
    if warn:
        print(f"⚠ {warn} warnings（无 error）")
        return 0
    print("✅ 0 errors, 0 warnings（配置完整）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
