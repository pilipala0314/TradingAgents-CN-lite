"""因子注册表 — 指标 → 数据需求的声明式映射（设计时产物）。

运行时由 loader 校验后加载，纯代码查表调 datasource.route()。
AI 只参与设计时编写/维护本注册表，不参与运行时取数决策。

用法::

    from foundation.registry import load_registry

    reg = load_registry("foundation/registry/sector_factors.yaml")
    for f in reg.factors:
        print(f.id, f.source_methods(), f.availability.profile.value)
"""
from foundation.registry.loader import (
    RegistryError,
    load_factor_specs,
    load_registry,
    validate_arg_signatures,
    validate_against_method_table,
)
from foundation.registry.name_maps import SECTOR_NAME_MAPS, mapped_sector
from foundation.registry.schema import (
    AvailabilitySpec,
    DisclosureProfile,
    ExtractKind,
    FactorRegistry,
    FactorSpec,
    FusionSpec,
    FusionStrategy,
    SourceSpec,
)

__all__ = [
    # name maps
    "SECTOR_NAME_MAPS",
    "mapped_sector",
    # schema
    "DisclosureProfile",
    "ExtractKind",
    "AvailabilitySpec",
    "SourceSpec",
    "FusionStrategy",
    "FusionSpec",
    "FactorSpec",
    "FactorRegistry",
    # loader
    "RegistryError",
    "load_registry",
    "load_factor_specs",
    "validate_against_method_table",
    "validate_arg_signatures",
]
