"""因子注册表加载器 — YAML 加载 + 与运行时数据源对账。

关键职责：**防止设计时声明漂移**。注册表里写的 `method` / `provider`
必须真实存在于 `datasource.method_table.FULL_METHOD_TABLE`，
否则加载期即报错——这把"AI 设计时产物"和"运行时能力"焊死在一起。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import ValidationError

from foundation.registry.schema import FactorRegistry, FactorSpec


class RegistryError(ValueError):
    """注册表校验失败。"""


def _method_table() -> dict[str, dict[str, str]]:
    """延迟导入 method_table，避免 registry 与 datasource 形成硬依赖。"""
    try:
        from datasource.method_table import FULL_METHOD_TABLE
    except ImportError as exc:  # pragma: no cover - datasource 缺失时
        raise RegistryError(
            "无法导入 datasource.method_table，无法对账 method/provider"
        ) from exc
    return FULL_METHOD_TABLE


def validate_against_method_table(registry: FactorRegistry) -> list[str]:
    """把注册表里的 method/provider 与路由表对账。

    Returns:
        问题清单（空 = 全部对得上）。不抛异常，供测试与自检工具聚合展示。
    """
    table = _method_table()
    problems: list[str] = []
    for factor in registry.factors:
        for provider, method in factor.source_methods():
            if method not in table:
                problems.append(
                    f"因子 {factor.id!r}: 方法 {method!r} 不在 FULL_METHOD_TABLE（provider={provider}）"
                )
                continue
            if provider not in table[method]:
                available = ", ".join(sorted(table[method])) or "(无)"
                problems.append(
                    f"因子 {factor.id!r}: 方法 {method!r} 不支持 provider {provider!r}；"
                    f"该方法的可用源为: {available}"
                )
    return problems


def _check_local_consistency(registry: FactorRegistry) -> list[str]:
    """注册表内部一致性检查（不依赖 datasource）。"""
    problems: list[str] = []
    seen_ids: set[str] = set()
    for f in registry.factors:
        if f.id in seen_ids:
            problems.append(f"因子 id 重复: {f.id!r}")
        seen_ids.add(f.id)

        # 多源因子不该用 SINGLE 策略（失去交叉验证意义）
        if f.is_multi_source and f.fusion.strategy.value == "single":
            problems.append(
                f"因子 {f.id!r}: 声明了 {len(f.sources)} 个源但 fusion.strategy=single，"
                f"无法做多源交叉验证"
            )
        # 单源因子不该要求多源一致
        if not f.is_multi_source and f.fusion.require_agreement:
            problems.append(f"因子 {f.id!r}: 单源因子不能 require_agreement=True")
        # 同因子内 provider 不应重复（无意义的重复声明）
        providers = [s.provider for s in f.sources]
        if len(providers) != len(set(providers)):
            problems.append(f"因子 {f.id!r}: sources 内 provider 重复 {providers}")
        # 取值方式与字段自洽 —— 仅在「声称已校准」时才构成错误。
        # 未校准（note 含「待校准」）是合法的中间状态，由 calibration_report() 跟踪。
        for s in f.sources:
            if not s.extraction_consistent and "待校准" not in s.note:
                problems.append(
                    f"因子 {f.id!r}.{s.provider}: extract_kind={s.extract_kind.value} "
                    f"要求 field 非空，但未标注「待校准」（声称已校准却字段缺失）"
                )
    return problems


def validate_arg_signatures(registry: FactorRegistry) -> list[str]:
    """校验声明的 args 键名与 provider 方法签名兼容。

    这是「参数名漂移」的防线：YAML 里写 `sector=` 但方法实参是 `sector_name`，
    不做校验只能到运行时才炸（TypeError: unexpected keyword）。
    """
    import inspect

    try:
        # 触发 provider 自动注册（@register_provider 在 import 时生效），
        # 否则 get_provider_class 全部返回 None，校验失效。
        import datasource.providers  # noqa: F401
        from datasource.registry import get_provider_class
    except ImportError:  # pragma: no cover
        return []

    problems: list[str] = []
    for factor in registry.factors:
        for source in factor.sources:
            cls = get_provider_class(source.provider)
            if cls is None:
                problems.append(
                    f"因子 {factor.id!r}: provider {source.provider!r} 未注册"
                )
                continue
            fn = getattr(cls, source.method, None)
            if fn is None:
                problems.append(
                    f"因子 {factor.id!r}: provider {source.provider!r} 无方法 {source.method!r}"
                )
                continue
            try:
                sig = inspect.signature(fn)
            except (TypeError, ValueError):  # pragma: no cover - 内建/装饰器方法
                continue

            params = sig.parameters
            has_var_kw = any(
                p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()
            )
            valid_names = {
                name for name, p in params.items()
                if p.kind in (
                    inspect.Parameter.POSITIONAL_OR_KEYWORD,
                    inspect.Parameter.KEYWORD_ONLY,
                )
            }
            for arg_name in source.args:
                if arg_name in valid_names or has_var_kw:
                    continue
                problems.append(
                    f"因子 {factor.id!r}.{source.provider}: 参数 {arg_name!r} "
                    f"不在 {source.method}() 签名中（可用: {sorted(valid_names) or '无'}）"
                )
            # 必填参数必须显式提供：args 缺必填项 → 运行时 TypeError，加载期就报出来
            required = {
                name for name, p in params.items()
                if p.kind in (
                    inspect.Parameter.POSITIONAL_OR_KEYWORD,
                    inspect.Parameter.KEYWORD_ONLY,
                )
                and p.default is inspect.Parameter.empty
                and name not in ("self", "cls")
            }
            missing = sorted(required - set(source.args))
            if missing:
                problems.append(
                    f"因子 {factor.id!r}.{source.provider}: 缺少必填参数 {missing}"
                    f"（{source.method}() 必填: {sorted(required) or '无'}）"
                )
    return problems


def load_registry(
    path: str | Path,
    *,
    check_method_table: bool = True,
) -> FactorRegistry:
    """从 YAML 加载并校验因子注册表。

    Raises:
        RegistryError: YAML 格式错误 / pydantic 校验失败 / 与路由表对不上
    """
    import yaml  # 延迟导入：registry 的 schema 层不需要 yaml

    p = Path(path)
    if not p.exists():
        raise RegistryError(f"注册表文件不存在: {p}")
    try:
        raw: Any = yaml.safe_load(p.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RegistryError(f"YAML 解析失败 {p}: {exc}") from exc

    try:
        registry = FactorRegistry.model_validate(raw)
    except ValidationError as exc:
        raise RegistryError(f"注册表 schema 校验失败 {p}:\n{exc}") from exc

    problems = _check_local_consistency(registry)
    if check_method_table:
        problems += validate_against_method_table(registry)
        problems += validate_arg_signatures(registry)
    if problems:
        joined = "\n  - ".join(problems)
        raise RegistryError(f"注册表校验未通过（{len(problems)} 项）:\n  - {joined}")
    return registry


def load_factor_specs(path: str | Path, **kwargs: Any) -> dict[str, FactorSpec]:
    """便捷入口：返回 {factor_id: FactorSpec}。"""
    return load_registry(path, **kwargs).by_id()


__all__ = [
    "RegistryError",
    "validate_against_method_table",
    "validate_arg_signatures",
    "load_registry",
    "load_factor_specs",
]
