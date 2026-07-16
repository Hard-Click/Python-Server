"""domain/shadow_report.py 순수 집계 테스트 (DB 불필요)."""
from domain.shadow_report import summarize_shadow_decisions


def _decision(variant, ext_delta, wk_delta, changed):
    return {
        "variant": variant,
        "extension_delta": ext_delta,
        "weekly_minutes_delta": wk_delta,
        "schedule_would_change": changed,
    }


def test_empty_returns_zero():
    assert summarize_shadow_decisions([]) == {"n": 0}


def test_basic_aggregation():
    decisions = [
        _decision(0.5, 0, 10.0, True),
        _decision(0.5, 1, 20.0, True),
        _decision(0.3, 0, 0.0, False),
    ]
    s = summarize_shadow_decisions(decisions)

    assert s["n"] == 3
    assert s["schedule_would_change_rate"] == round(100 * 2 / 3, 1)
    assert s["extension_increase_rate"] == round(100 * 1 / 3, 1)
    assert s["extension_delta_distribution"] == {0: 2, 1: 1}
    assert s["weekly_minutes_delta"]["max"] == 20.0
    assert s["weekly_minutes_delta"]["mean"] == 10.0
    assert s["quality"] is None  # quality_delta 없는 로그만 있으면 품질 요약도 없어야 함


def test_quality_delta_aggregated_only_when_present():
    decisions = [
        _decision(0.5, 0, 10.0, True),
        _decision(0.5, 0, 10.0, True),
    ]
    decisions[0]["quality_delta"] = {
        "load_cv_delta": 0.1, "overloaded_weeks_delta": 1,
        "max_consecutive_overload_delta": 1, "peak_subject_share_delta": 0.2,
    }
    decisions[1]["quality_delta"] = None  # 이 건은 infeasible 케이스라 품질 비교 대상 아님

    s = summarize_shadow_decisions(decisions)

    assert s["quality"]["n"] == 1  # None인 건 집계에서 빠져야 함
    assert s["quality"]["load_cv_worsened_rate"] == 100.0
    assert s["quality"]["overloaded_weeks_worsened_rate"] == 100.0
    assert s["quality"]["peak_subject_share_delta_mean"] == 0.2


def test_by_variant_breakdown():
    decisions = [
        _decision(0.5, 1, 20.0, True),
        _decision(0.5, 0, 10.0, True),
        _decision(0.3, 0, 0.0, False),
    ]
    s = summarize_shadow_decisions(decisions)

    assert s["by_variant"][0.5]["n"] == 2
    assert s["by_variant"][0.5]["would_change_rate"] == 100.0
    assert s["by_variant"][0.5]["extension_increase_rate"] == 50.0
    assert s["by_variant"][0.3]["n"] == 1
    assert s["by_variant"][0.3]["would_change_rate"] == 0.0
