"""Phase 1 반례 배터리의 '하드 불변식' 가드 (pass/fail).

scripts/redteam_stretch_factor_battery.py가 위험 sf '구간을 좁히는' 분석이라면, 여기는 어떤 sf든
절대 깨지면 안 되는 불변식을 못박는다. tests/test_domain.py가 이미 클램프·콜드스타트<3→1.0·
sf 단조성·경계값(sf=0→1.0, sf=1→raw)을 커버하므로, 여기선 그 위의 '결정 안정성' 계열만 다룬다.
"""
import math

from domain.scheduler import compute_efficiency_coefficient, MIN_EFFICIENCY_SAMPLES

SWEEP = [round(0.05 * i, 2) for i in range(0, 21)]


def _completed(raw, n):
    return [{"expected_duration_min": 100, "actual_duration_min": 100 * raw}] * n


def test_cold_start_coeff_invariant_to_stretch_factor():
    """표본<MIN인 콜드스타트 학생은 sf를 뭘 줘도 coeff가 1.0이어야 한다 - sf가 콜드스타트
    학생의 스케줄을 흔들면 안 됨(분석에서 '콜드스타트 버킷 sf 무관 0%'의 근거)."""
    completed = _completed(1.5, MIN_EFFICIENCY_SAMPLES - 1)
    for sf in SWEEP:
        assert compute_efficiency_coefficient(completed, stretch_factor=sf) == 1.0


def test_coeff_is_lipschitz_in_raw_for_all_stretch_factors():
    """raw가 조금(예: 0.05) 변할 때 coeff 변화가 그 폭을 절대 넘지 않아야 한다(sf<=1이므로).
    이게 깨지면 관측치 미세오차에 스케줄이 벼랑처럼 튄다 - 배터리 '불안정성' 축의 하드 보장."""
    for sf in SWEEP:
        for raw in [0.6, 0.9, 1.0, 1.4, 1.95, 2.5]:
            c1 = compute_efficiency_coefficient(_completed(raw, 5), stretch_factor=sf)
            c2 = compute_efficiency_coefficient(_completed(raw + 0.05, 5), stretch_factor=sf)
            assert abs(c2 - c1) <= 0.05 + 1e-9


def test_higher_stretch_never_reduces_coeff_for_observed_slow_student():
    """관측상 느린(raw>1) 학생은 sf를 올릴수록 coeff가 내려가면 안 된다(단조 비감소).
    거꾸로 가면 '더 반영했는데 부하가 줄어드는' 역설이 생겨 방향성이 깨짐."""
    prev = None
    for sf in SWEEP:
        c = compute_efficiency_coefficient(_completed(1.5, 5), stretch_factor=sf)
        if prev is not None:
            assert c >= prev - 1e-9
        prev = c


def test_zero_stretch_is_maximal_underreaction():
    """sf=0은 관측 지체를 완전히 무시(coeff=1.0)한다 - 배터리 '과소반응' 축이 sf=0에서
    최대여야 한다는 하드 방향성. 이게 저sf 위험의 근거."""
    assert compute_efficiency_coefficient(_completed(1.6, 5), stretch_factor=0.0) == 1.0
    assert compute_efficiency_coefficient(_completed(1.6, 5), stretch_factor=0.5) > 1.0
