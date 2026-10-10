"""L2 落盘 — 指标包路径与原子写（tmp + fsync + os.replace）。

路径约定（设计文档 §3.4）：``reports/<trade_date>/artifacts/indicators/sector_<slug>_g<round>.json``；
重跑覆盖不叠加，杜绝半写产物。
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from foundation.schemas import IndicatorPack

# Windows 非法字符（保留 CJK）
_UNSAFE = '\\/:*?"<>|'


def _slug(text: str) -> str:
    out = "".join("_" if ch in _UNSAFE else ch for ch in text).strip().strip(".")
    return out or "unknown"


def pack_path(
    reports_dir: str | Path, trade_date: str, sector: str, generation_round: int,
) -> Path:
    """板块 pack 的目标路径。trade_date 使用 ``YYYY-MM-DD``。"""
    return (
        Path(reports_dir)
        / trade_date
        / "artifacts"
        / "indicators"
        / f"sector_{_slug(sector)}_g{int(generation_round)}.json"
    )


def write_pack(pack: IndicatorPack, path: Path) -> Path:
    """原子写 pack（同目录 tmp → fsync → os.replace），返回最终路径。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(pack.model_dump(mode="json"), ensure_ascii=False, indent=2)
    tmp = path.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(payload)
        f.flush()
        os.fsync(f.fileno())
    os.replace(str(tmp), str(path))
    return path


__all__ = ["pack_path", "write_pack"]