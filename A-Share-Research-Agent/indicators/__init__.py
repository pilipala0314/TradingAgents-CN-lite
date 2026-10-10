"""L2 指标层 — 源级融合模块（设计文档 §4）。

使命：读注册表（foundation/registry/sector_factors.yaml）→ 锁定 provider 取数
→ 按 extract_kind 提取 → 按 fusion 策略融合 → 产出可溯源的 IndicatorPack 并原子落盘。

用法::

    from indicators import build_sector_pack, RunConfig

    pack = await build_sector_pack("半导体", "2026-09-30", generation_round=2)
"""
from indicators.pipeline import RunConfig, build_sector_pack

__all__ = ["RunConfig", "build_sector_pack"]