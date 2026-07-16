"""domain/stretch_guardrails.py 순수 테스트 - 결과 기준 가드레일 + hard/soft + abstain (DB 불필요)."""
from domain.stretch_guardrails import (
    evaluate_guardrails, WEEKLY_LOAD_SURGE_CAP_PCT, WEEKLY_LOAD_SURGE_ABS_MIN,
)
from domain.scheduler import MIN_EFFICIENCY_SAMPLES


def test_allow_when_no_risk():
    r = evaluate_guardrails(
        applied_feasible=True, shadow_feasible=True, applied_ext=0, shadow_ext=0,
        completed_counts=[5, 5], weekly_delta_pct=1.0, weekly_delta_abs=3.0,
    )
    assert r["triggered"] is False
    assert r["action"] == "allow"
    assert r["would_have_failed"] is False


def test_infeasibility_is_hard_and_flagged_would_have_failed():
    # baseline 완주 가능, shadow 완주 불가(실제 CP-SAT 결과) -> 치명, 무조건 폴백
    r = evaluate_guardrails(
        applied_feasible=True, shadow_feasible=False, applied_ext=0, shadow_ext=2,
        completed_counts=[5], weekly_delta_pct=10.0, weekly_delta_abs=40.0,
    )
    assert "infeasibility" in r["hard"]
    assert r["would_have_failed"] is True
    assert r["action"] == "fallback_baseline"


def test_soft_only_yields_dampen_not_fallback():
    # 연장 증가 + 부하 급증(사고는 아님) -> soft -> dampen
    r = evaluate_guardrails(
        applied_feasible=True, shadow_feasible=True, applied_ext=0, shadow_ext=1,
        completed_counts=[5], weekly_delta_pct=WEEKLY_LOAD_SURGE_CAP_PCT + 5,
        weekly_delta_abs=WEEKLY_LOAD_SURGE_ABS_MIN + 10,
    )
    assert "extension_increase" in r["soft"]
    assert "weekly_load_surge" in r["soft"]
    assert not r["hard"]
    assert r["action"] == "dampen"


def test_weekly_surge_needs_both_pct_and_abs():
    # 퍼센트만 크고 절대 증가는 미미 -> 급증 아님(작은 baseline 과장 방지, #4)
    r = evaluate_guardrails(
        applied_feasible=True, shadow_feasible=True, applied_ext=0, shadow_ext=0,
        completed_counts=[5], weekly_delta_pct=WEEKLY_LOAD_SURGE_CAP_PCT + 50,
        weekly_delta_abs=WEEKLY_LOAD_SURGE_ABS_MIN - 5,
    )
    assert "weekly_load_surge" not in r["soft"]
    assert r["action"] == "allow"


def test_abstain_only_when_all_courses_cold_start():
    # 모든 코스 콜드스타트 -> abstain(hard)
    r = evaluate_guardrails(
        applied_feasible=True, shadow_feasible=True, applied_ext=0, shadow_ext=0,
        completed_counts=[MIN_EFFICIENCY_SAMPLES - 1, 1], weekly_delta_pct=0.0, weekly_delta_abs=0.0,
    )
    assert "abstain_no_signal" in r["hard"]
    assert r["action"] == "fallback_baseline"


def test_no_abstain_when_one_course_has_signal():
    # 한 코스라도 신호 충분하면 abstain 안 함(성숙 코스 신호 안 버림)
    r = evaluate_guardrails(
        applied_feasible=True, shadow_feasible=True, applied_ext=0, shadow_ext=0,
        completed_counts=[MIN_EFFICIENCY_SAMPLES, 1], weekly_delta_pct=0.0, weekly_delta_abs=0.0,
    )
    assert "abstain_no_signal" not in r["hard"]
    assert r["action"] == "allow"
