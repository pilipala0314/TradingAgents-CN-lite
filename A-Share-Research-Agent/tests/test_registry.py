"""因子注册表测试 — schema 校验 + 与运行时数据源对账。

核心验证：设计时声明（YAML）与运行时能力（FULL_METHOD_TABLE）必须焊死，
声明漂移在加载期就报错，而不是运行时才炸。
"""
from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from foundation.registry import (
    AvailabilitySpec,
    DisclosureProfile,
    ExtractKind,
    FactorRegistry,
    FactorSpec,
    FusionSpec,
    FusionStrategy,
    RegistryError,
    SourceSpec,
    load_registry,
    validate_arg_signatures,
    validate_against_method_table,
)
from foundation.schemas.common import Caliber
from foundation.schemas.factor import FactorDimension

REGISTRY_PATH = (
    Path(__file__).resolve().parent.parent
    / "foundation" / "registry" / "sector_factors.yaml"
)


def _source(provider="akshare", method="get_sector_fund_flow", **kw) -> SourceSpec:
    return SourceSpec(provider=provider, method=method, caliber=Caliber.AKSHARE, **kw)


def _factor(**kw) -> FactorSpec:
    base = dict(
        id="f1",
        dimension=FactorDimension.CAPITAL,
        name="测试因子",
        availability=AvailabilitySpec(profile=DisclosureProfile.AFTER_CLOSE, available_from="15:30"),
        sources=[_source()],
    )
    base.update(kw)
    return FactorSpec(**base)


# ── schema 校验 ────────────────────────────────────────────────────


@pytest.mark.unit
class TestAvailabilitySpec:
    def test_second_round_requires_at(self):
        with pytest.raises(ValidationError):
            AvailabilitySpec(profile=DisclosureProfile.EVENING, second_round=True)

    def test_second_round_with_at_ok(self):
        a = AvailabilitySpec(
            profile=DisclosureProfile.EVENING,
            second_round=True, second_round_at="20:30",
        )
        assert a.second_round_at == "20:30"

    def test_time_pattern_enforced(self):
        for bad in ("25:99", "15:60", "9:30", "1530", "25:00"):
            with pytest.raises(ValidationError):
                AvailabilitySpec(profile=DisclosureProfile.AFTER_CLOSE, available_from=bad)

    def test_time_boundary_valid(self):
        for ok in ("00:00", "15:30", "23:59"):
            assert AvailabilitySpec(
                profile=DisclosureProfile.AFTER_CLOSE, available_from=ok
            ).available_from == ok


@pytest.mark.unit
class TestSourceSpec:
    def test_default_uncalibrated(self):
        s = _source(field="")
        assert s.is_calibrated is False

    def test_calibrated_when_field_set(self):
        s = _source(field="change_pct")
        assert s.is_calibrated is True

    def test_note_todo_marks_uncalibrated(self):
        s = _source(field="x", note="待校准 — 返回字段需实测确认")
        assert s.is_calibrated is False

    def test_template_args_allowed(self):
        s = _source(args={"sector": "{sector}", "limit": 90})
        assert s.args["sector"] == "{sector}"


@pytest.mark.unit
class TestFusionSpec:
    def test_default_single(self):
        assert FusionSpec().strategy is FusionStrategy.SINGLE

    def test_conflict_threshold_bounds(self):
        with pytest.raises(ValidationError):
            FusionSpec(conflict_threshold=-0.1)


@pytest.mark.unit
class TestFactorSpec:
    def test_requires_at_least_one_source(self):
        with pytest.raises(ValidationError):
            _factor(sources=[])

    def test_multi_source_detection(self):
        f = _factor(sources=[_source("akshare"), _source("eastmoney", method="get_individual_fund_flow")])
        assert f.is_multi_source is True

    def test_source_methods_shape(self):
        f = _factor(sources=[_source("akshare"), _source("eastmoney", method="get_individual_fund_flow")])
        assert f.source_methods() == [
            ("akshare", "get_sector_fund_flow"),
            ("eastmoney", "get_individual_fund_flow"),
        ]


# ── loader 一致性校验 ──────────────────────────────────────────────


@pytest.mark.unit
class TestLocalConsistency:
    def _reg(self, factors) -> FactorRegistry:
        return FactorRegistry(name="t", factors=factors)

    def test_duplicate_id_rejected(self):
        reg = self._reg([_factor(id="a"), _factor(id="a")])
        from foundation.registry.loader import _check_local_consistency
        problems = _check_local_consistency(reg)
        assert any("重复" in p for p in problems)

    def test_multi_source_with_single_strategy_flagged(self):
        f = _factor(
            sources=[_source("akshare"), _source("eastmoney", method="get_individual_fund_flow")],
            fusion=FusionSpec(strategy=FusionStrategy.SINGLE),
        )
        from foundation.registry.loader import _check_local_consistency
        problems = _check_local_consistency(self._reg([f]))
        assert any("无法做多源交叉验证" in p for p in problems)

    def test_single_source_cannot_require_agreement(self):
        f = _factor(fusion=FusionSpec(require_agreement=True))
        from foundation.registry.loader import _check_local_consistency
        problems = _check_local_consistency(self._reg([f]))
        assert any("单源因子不能 require_agreement" in p for p in problems)

    def test_duplicate_provider_flagged(self):
        f = _factor(sources=[_source("akshare"), _source("akshare", method="get_market_fund_flow")])
        from foundation.registry.loader import _check_local_consistency
        problems = _check_local_consistency(self._reg([f]))
        assert any("provider 重复" in p for p in problems)

    def test_value_without_field_flagged_only_when_claimed_calibrated(self):
        """声称已校准却缺 field → 报错；标注「待校准」→ 合法中间状态。"""
        from foundation.registry.loader import _check_local_consistency

        # 无待校准标注 + field 为空 → 报错
        f_claimed = _factor(sources=[_source("akshare", field="", note="")])
        problems = _check_local_consistency(self._reg([f_claimed]))
        assert any("声称已校准却字段缺失" in p for p in problems)

        # 标注待校准 → 不报错
        f_pending = _factor(sources=[_source("akshare", field="", note="待校准 — 需实测")])
        assert _check_local_consistency(self._reg([f_pending])) == []


@pytest.mark.unit
class TestExtractKind:
    """extract_kind 决定 field/match 的必填语义。"""

    def test_value_requires_field(self):
        assert _source(field="x", extract_kind=ExtractKind.VALUE).extraction_consistent is True
        assert _source(field="", extract_kind=ExtractKind.VALUE).extraction_consistent is False

    def test_sum_requires_field(self):
        assert _source(field="", extract_kind=ExtractKind.SUM).extraction_consistent is False
        assert _source(field="main_net", extract_kind=ExtractKind.SUM).extraction_consistent is True

    def test_count_and_whole_do_not_require_field(self):
        assert _source(field="", extract_kind=ExtractKind.COUNT).extraction_consistent is True
        assert _source(field="", extract_kind=ExtractKind.WHOLE).extraction_consistent is True

    def test_whole_is_calibrated_without_field(self):
        """整体取用：无 field 但探针成功即视为已校准。"""
        assert _source(field="", extract_kind=ExtractKind.WHOLE).is_calibrated is True

    def test_calibrated_requires_consistent_and_no_todo(self):
        s = _source(field="x", extract_kind=ExtractKind.VALUE, note="已实测")
        assert s.is_calibrated is True
        s_todo = _source(field="x", extract_kind=ExtractKind.VALUE, note="待校准")
        assert s_todo.is_calibrated is False


@pytest.mark.unit
class TestArgSignatureValidation:
    """参数名漂移防线：YAML 的 args 键必须在方法签名中。"""

    def test_wrong_arg_name_detected(self):
        from foundation.registry.loader import validate_arg_signatures

        # get_sector_recent_bars(sector_name, days) 无 **kwargs → 'sector' 必被拦
        f = _factor(
            id="x",
            sources=[
                _source(
                    "akshare",
                    method="get_sector_recent_bars",
                    args={"sector": "{sector}"},  # 应为 sector_name
                )
            ],
        )
        problems = validate_arg_signatures(FactorRegistry(name="t", factors=[f]))
        # 两个问题：① 参数名 'sector' 漂移；② 必填参数 'sector_name' 缺失
        assert len(problems) == 2
        assert any("不在 get_sector_recent_bars() 签名中" in p for p in problems)
        assert any("缺少必填参数 ['sector_name']" in p for p in problems)

    def test_missing_required_arg_detected(self):
        """args 缺必填参数 → 加载期报错（防运行时 TypeError）。"""
        from foundation.registry.loader import validate_arg_signatures

        f = _factor(
            id="y",
            sources=[_source("akshare", method="get_sector_recent_bars", args={})],
        )
        problems = validate_arg_signatures(FactorRegistry(name="t", factors=[f]))
        assert any("缺少必填参数" in p and "sector_name" in p for p in problems)

    def test_correct_arg_name_passes(self):
        from foundation.registry.loader import validate_arg_signatures

        f = _factor(
            id="x",
            sources=[
                _source("akshare", method="get_sector_recent_bars", args={"sector_name": "{sector}"})
            ],
        )
        assert validate_arg_signatures(FactorRegistry(name="t", factors=[f])) == []

    def test_real_registry_passes_signature_check(self):
        from foundation.registry.loader import validate_arg_signatures

        reg = load_registry(REGISTRY_PATH)
        assert validate_arg_signatures(reg) == []


# ── 与运行时数据源对账（核心机制）────────────────────────────────


@pytest.mark.unit
class TestMethodTableReconciliation:
    """设计时声明必须能对上运行时能力 — 防止声明漂移。"""

    def _reg(self, factors) -> FactorRegistry:
        return FactorRegistry(name="t", factors=factors)

    def test_unknown_method_detected(self):
        f = _factor(sources=[_source("akshare", method="get_nonexistent_api")])
        problems = validate_against_method_table(self._reg([f]))
        assert len(problems) == 1
        assert "不在 FULL_METHOD_TABLE" in problems[0]

    def test_wrong_provider_detected(self):
        """方法存在但该 provider 不支持 → 也要报错。"""
        f = _factor(sources=[_source("tushare", method="get_sector_fund_flow")])
        problems = validate_against_method_table(self._reg([f]))
        assert len(problems) == 1
        assert "不支持 provider" in problems[0]

    def test_valid_mapping_passes(self):
        f = _factor(sources=[_source("akshare", method="get_sector_fund_flow")])
        assert validate_against_method_table(self._reg([f])) == []


# ── 真实注册表 ─────────────────────────────────────────────────────


@pytest.mark.unit
class TestRealRegistry:
    def test_file_exists(self):
        assert REGISTRY_PATH.exists(), f"注册表缺失: {REGISTRY_PATH}"

    def test_loads_and_reconciles(self):
        reg = load_registry(REGISTRY_PATH)
        assert len(reg.factors) == 15
        # 每个因子的 method/provider 都能对上 89 API
        assert validate_against_method_table(reg) == []

    def test_covers_all_five_dimensions(self):
        reg = load_registry(REGISTRY_PATH)
        dims = set(reg.by_dimension().keys())
        assert {"price", "capital", "sentiment", "event", "market"} <= dims

    def test_has_multi_source_factors(self):
        reg = load_registry(REGISTRY_PATH)
        multi = reg.multi_source_factors()
        assert len(multi) >= 3, "板块报告应有多个多源因子做交叉验证"

    def test_has_second_round_factors(self):
        """T 日盘后必然有延迟数据，必须有二次刷新清单。"""
        reg = load_registry(REGISTRY_PATH)
        assert len(reg.second_round_factors()) >= 3

    def test_calibration_report_shape(self):
        reg = load_registry(REGISTRY_PATH)
        r = reg.calibration_report()
        assert r["sources_total"] > 0
        assert 0.0 <= r["calibration_rate"] <= 1.0
        assert isinstance(r["uncalibrated"], list)

    def test_yaml_is_design_artifact(self):
        """YAML 是设计时产物：带版本号、可 diff。"""
        text = REGISTRY_PATH.read_text(encoding="utf-8")
        assert 'version: "1.0"' in text
        assert "factors:" in text


@pytest.mark.unit
class TestLoaderErrors:
    def test_missing_file(self):
        with pytest.raises(RegistryError):
            load_registry("nonexistent.yaml")

    def test_bad_yaml(self, tmp_path):
        p = tmp_path / "bad.yaml"
        p.write_text("factors: [:::", encoding="utf-8")
        with pytest.raises(RegistryError):
            load_registry(p)

    def test_schema_violation(self, tmp_path):
        p = tmp_path / "bad.yaml"
        p.write_text("name: t\nfactors: []\n", encoding="utf-8")
        with pytest.raises(RegistryError):
            load_registry(p)

    def test_method_table_drift_raises(self, tmp_path):
        """写一个引用不存在 API 的注册表 → 加载期即失败。"""
        p = tmp_path / "drift.yaml"
        p.write_text(
            'name: t\nfactors:\n'
            '  - id: x\n    dimension: price\n    name: x\n'
            '    availability: {profile: after_close, available_from: "15:30"}\n'
            '    sources:\n      - provider: akshare\n'
            '        method: get_removed_api\n        caliber: akshare\n',
            encoding="utf-8",
        )
        with pytest.raises(RegistryError) as exc:
            load_registry(p)
        assert "get_removed_api" in str(exc.value)
