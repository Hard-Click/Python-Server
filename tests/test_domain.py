"""domain/ 순수 로직만 검증 - DB/네트워크 전혀 필요 없음."""
from datetime import date, datetime, timedelta, timezone

from domain.scheduler import (
    generate_weekly_schedule, generate_unified_weekly_schedule, compute_num_weeks,
    compute_efficiency_coefficient, compute_required_extension_weeks,
)
from domain.review import quiz_score_to_grade, review_lesson
from domain.risk import compute_rule_based_risk, risk_label
from domain.reflow import compute_slip_status, redistribute_remaining_week
from domain.experiments import assign_variant


def test_scheduler_respects_prerequisites_deadline_cap():
    lessons = [
        {"id": "math_1", "duration_min": 60, "deadline_week": 3},
        {"id": "math_2", "duration_min": 60, "deadline_week": 3},
        {"id": "math_3", "duration_min": 90, "deadline_week": 3},
    ]
    prerequisites = [("math_1", "math_2"), ("math_2", "math_3")]
    result = generate_weekly_schedule(lessons, [180, 180, 180, 180], prerequisites)
    assert result is not None
    assert result["math_1"] <= result["math_2"] <= result["math_3"]


def test_scheduler_pushes_back_when_cap_too_tight():
    lessons = [{"id": "a", "duration_min": 60, "deadline_week": 3}]
    result = generate_weekly_schedule(lessons, [10, 180, 180, 180])
    assert result["a"] != 0  # 이번 주 cap이 부족하니 뒤로 밀림


def test_efficiency_coefficient_defaults_to_one_below_min_samples():
    completed = [{"expected_duration_min": 30, "actual_duration_min": 60}] * 2  # 2건 < MIN_EFFICIENCY_SAMPLES(3)
    assert compute_efficiency_coefficient(completed) == 1.0


def test_efficiency_coefficient_stretches_toward_baseline_instead_of_raw_pace():
    # 실측(raw)은 1.5배 느림이지만, 스트레치(절반 반영)로 최종 계수는 1.25가 돼야 함
    # (1.0 + 0.5*(1.5-1.0)) - 학생의 느린 페이스를 100% 그대로 순응하지 않는다는 확정 정책.
    completed = [{"expected_duration_min": 20, "actual_duration_min": 30}] * 5
    assert compute_efficiency_coefficient(completed) == 1.25


def test_efficiency_coefficient_clamps_extreme_values():
    completed = [{"expected_duration_min": 10, "actual_duration_min": 100}] * 5  # raw 10배 -> 스트레치해도 5.5, 그래도 과함
    assert compute_efficiency_coefficient(completed) == 2.0  # MAX_EFFICIENCY_COEFFICIENT


def test_efficiency_coefficient_accepts_stretch_factor_override():
    """A/B 실험이 학생별로 다른 stretch_factor를 배정하므로 override 가능해야 함."""
    completed = [{"expected_duration_min": 20, "actual_duration_min": 30}] * 5  # raw = 1.5
    weak_stretch = compute_efficiency_coefficient(completed, stretch_factor=0.3)  # 1.0+0.3*0.5=1.15
    strong_stretch = compute_efficiency_coefficient(completed, stretch_factor=0.7)  # 1.0+0.7*0.5=1.35
    assert weak_stretch == 1.15
    assert strong_stretch == 1.35
    assert weak_stretch != strong_stretch


def test_efficiency_coefficient_monotonic_in_stretch_factor():
    """'0.5가 최적이다'를 증명하는 게 아니라, 공식 자체가 안 무너진다는 걸 못박는 속성 검증.
    stretch_factor가 0->1로 커질수록 최종 계수는 단조롭게 raw 쪽으로 움직여야 한다
    (중간에 뒤집히거나 진동하면 그건 최적화 문제가 아니라 구현 버그)."""
    completed = [{"expected_duration_min": 20, "actual_duration_min": 30}] * 5  # raw = 1.5
    results = [compute_efficiency_coefficient(completed, stretch_factor=f) for f in [0.0, 0.3, 0.5, 0.7, 1.0]]
    assert results == sorted(results)  # 단조 증가


def test_efficiency_coefficient_boundary_values_are_exact():
    """stretch_factor=0이면 raw와 무관하게 항상 정확히 1.0(강사 추정치 100% 신뢰),
    stretch_factor=1이면 항상 정확히 raw(관측치 100% 신뢰) - 이 두 극단이 안 맞으면 공식이 잘못된 것."""
    completed = [{"expected_duration_min": 20, "actual_duration_min": 30}] * 5  # raw = 1.5
    assert compute_efficiency_coefficient(completed, stretch_factor=0.0) == 1.0
    assert compute_efficiency_coefficient(completed, stretch_factor=1.0) == 1.5


def test_efficiency_coefficient_stretch_factor_irrelevant_when_pace_matches_estimate():
    """학생 페이스가 강사 추정치와 거의 같으면(raw≈1.0), stretch_factor를 뭘 줘도 결과가
    거의 같아야 한다 - "이 학생한테는 stretch_factor 선택 자체가 중요하지 않다"는 뜻이고,
    이게 바로 stretch_factor 불확실성이 실제로 문제되는 대상(raw가 1.0에서 먼 학생)만
    한정된다는 걸 보여준다."""
    completed = [{"expected_duration_min": 20, "actual_duration_min": 20.4}] * 5  # raw = 1.02
    results = [compute_efficiency_coefficient(completed, stretch_factor=f) for f in [0.3, 0.5, 0.7]]
    assert max(results) - min(results) < 0.02


def test_efficiency_coefficient_differs_per_course_when_grouped():
    """같은 학생인데 수학은 느리고(raw 1.5배 -> 스트레치 1.25) 영어는 빠른(raw 0.7배 -> 스트레치 0.85) 경우 -
    코스별로 나눠서 넣으면(application 레이어가 하는 것과 동일하게) 각 코스에 맞는 계수가 따로 나와야 한다.
    학생 전체를 하나로 합쳐서 계산하면 이 둘이 서로를 오염시켜 평균값만 나옴."""
    completed = (
        [{"course_id": "math", "expected_duration_min": 20, "actual_duration_min": 30}] * 4
        + [{"course_id": "eng", "expected_duration_min": 20, "actual_duration_min": 14}] * 4
    )
    by_course = {}
    for row in completed:
        by_course.setdefault(row["course_id"], []).append(row)

    math_coefficient = compute_efficiency_coefficient(by_course["math"])
    eng_coefficient = compute_efficiency_coefficient(by_course["eng"])
    assert math_coefficient == 1.25
    assert eng_coefficient == 0.85
    assert math_coefficient != eng_coefficient


def test_required_extension_weeks_is_zero_when_no_shortfall():
    course_totals = [{"total_duration_min": 400, "deadline_week": 3}]  # 4주 * 420 = 1680분 여유
    assert compute_required_extension_weeks(course_totals, total_weekly_minutes=420, max_extension_weeks=2) == 0


def test_required_extension_weeks_computes_actual_need_not_a_fixed_guess():
    # 4주(420*4=1680분) 확보됐는데 2000분 필요 -> 부족분 320분 -> 420분/주로 나누면 1주만 더 있으면 됨
    course_totals = [{"total_duration_min": 2000, "deadline_week": 3}]
    assert compute_required_extension_weeks(course_totals, total_weekly_minutes=420, max_extension_weeks=2) == 1


def test_required_extension_weeks_clamped_to_hard_cap():
    # 부족분이 커서 3주가 필요해도 하드캡(2주)을 넘길 수 없음 - 그 이상은 "물리적으로 불가능"으로 처리
    course_totals = [{"total_duration_min": 3000, "deadline_week": 3}]  # 1680분 확보, 부족분 1320분 -> 필요 4주
    assert compute_required_extension_weeks(course_totals, total_weekly_minutes=420, max_extension_weeks=2) == 2


def test_required_extension_weeks_uses_worst_course_among_several():
    course_totals = [
        {"total_duration_min": 400, "deadline_week": 3},  # 여유
        {"total_duration_min": 2000, "deadline_week": 3},  # 1주 더 필요
    ]
    assert compute_required_extension_weeks(course_totals, total_weekly_minutes=420, max_extension_weeks=2) == 1


def test_unified_schedule_survives_where_grade_based_split_would_fail():
    """등급이 같은 두 코스인데 마감까지 남은 주차가 다른 경우 - 기존 "등급 비율로
    예산 사전분배" 방식이면 균등분배(210/210)돼서 4주밖에 안 남은 수학(주당 750분 필요)이
    INFEASIBLE로 터졌던 상황. 통합 모델은 예산을 사전분배하지 않고 deadline_week 제약만으로
    풀기 때문에 같은 상황에서 살아남아야 한다."""
    lessons = (
        [{"id": f"math_{i}", "course_id": "math", "duration_min": 100, "deadline_week": 3} for i in range(12)]
        + [{"id": f"eng_{i}", "course_id": "eng", "duration_min": 40, "deadline_week": 9} for i in range(10)]
    )
    # 수학: 12*100=1200분/4주 -> 주당 최소 300분(균등분배 210으로는 부족). 영어: 10*40=400분/10주 -> 주당 40분이면 충분.
    weekly_caps = [420] * 10  # 학생 전체 공유 주간 가용시간(기존 TODO 플레이스홀더 값과 동일)
    grade_weights = {"math": 3, "eng": 3}  # 등급 동일해도 마감 압박은 다름

    result = generate_unified_weekly_schedule(lessons, weekly_caps, grade_weights=grade_weights)
    assert result is not None
    assert all(result[f"math_{i}"] <= 3 for i in range(12))  # 수학은 4주 안에 전부 끝남
    assert all(result[f"eng_{i}"] <= 9 for i in range(10))


def test_unified_schedule_prefers_earlier_weeks_for_worse_grade_course():
    lessons = [
        {"id": "math_1", "course_id": "math", "duration_min": 60, "deadline_week": 5},
        {"id": "eng_1", "course_id": "eng", "duration_min": 60, "deadline_week": 5},
    ]
    weekly_caps = [60] * 6  # 한 주에 하나씩만 들어감 -> 어느 걸 먼저 배정할지가 갈림
    grade_weights = {"math": 8, "eng": 2}  # 수학이 훨씬 나쁨 -> 더 일찍 끝내는 걸 선호해야 함

    result = generate_unified_weekly_schedule(lessons, weekly_caps, grade_weights=grade_weights)
    assert result is not None
    assert result["math_1"] < result["eng_1"]


def test_unified_schedule_enforces_per_course_weekly_cap():
    """코스별 주간 상한(강사 입력 '하루 최대 학습 시간' 환산)이 있으면 전체 주간 캡이 넉넉해도
    한 과목이 한 주에 몰리지 않고 상한 안에서 여러 주로 분산돼야 한다."""
    lessons = [{"id": f"c1_{i}", "course_id": "c1", "duration_min": 100, "deadline_week": 4} for i in range(3)]
    weekly_caps = [1000] * 5  # 전체 캡만 보면 3강 전부 1주에 들어감

    # 상한 없으면 한 주(week 0)에 몰림
    no_cap = generate_unified_weekly_schedule(lessons, weekly_caps)
    assert len(set(no_cap.values())) == 1

    # 코스별 주간 상한 100분 -> 주당 1강만 가능 -> 3주로 분산 + 어느 주도 상한 초과 없음
    capped = generate_unified_weekly_schedule(lessons, weekly_caps, course_weekly_caps={"c1": 100})
    assert capped is not None
    per_week_load = {}
    for lesson in lessons:
        per_week_load[capped[lesson["id"]]] = per_week_load.get(capped[lesson["id"]], 0) + lesson["duration_min"]
    assert all(load <= 100 for load in per_week_load.values())
    assert len(per_week_load) == 3  # 세 주로 흩어짐


def test_unified_schedule_infeasible_when_per_course_cap_below_single_lesson():
    """코스 상한이 강의 한 개 길이보다 작으면 그 강의를 어느 주에도 못 넣어 완주 불가(None).
    이걸로 호출부(_solve_with_extension)의 연장·불가 알림 경로가 트리거된다."""
    lessons = [{"id": "c1_0", "course_id": "c1", "duration_min": 100, "deadline_week": 4}]
    result = generate_unified_weekly_schedule(lessons, [1000] * 5, course_weekly_caps={"c1": 50})
    assert result is None


def test_compute_num_weeks_uses_suneung_review_buffer_when_tighter():
    today = date(2026, 1, 1)
    enrolled_at = today
    # target_weeks가 널널해도(50주) 수능일-3주 버퍼가 더 타이트하면 그게 하드 상한이 돼야 함
    suneung_date = today + timedelta(weeks=10)
    num_weeks = compute_num_weeks(today, enrolled_at, target_weeks=50, suneung_date=suneung_date)
    assert num_weeks == 7  # 10주 - 3주 리뷰버퍼


def test_compute_num_weeks_uses_target_plus_slip_when_tighter():
    today = date(2026, 1, 1)
    enrolled_at = today
    suneung_date = today + timedelta(weeks=52)  # 널널함
    num_weeks = compute_num_weeks(today, enrolled_at, target_weeks=4, suneung_date=suneung_date)
    assert num_weeks == 6  # 4주 + 2주 슬립버퍼


def test_compute_num_weeks_falls_back_to_suneung_when_target_weeks_missing():
    today = date(2026, 1, 1)
    enrolled_at = today
    suneung_date = today + timedelta(weeks=8)
    num_weeks = compute_num_weeks(today, enrolled_at, target_weeks=None, suneung_date=suneung_date)
    assert num_weeks == 5  # 8주 - 3주 리뷰버퍼, 온보딩 미완료 콜드스타트 폴백


def test_compute_num_weeks_never_goes_below_one_week():
    today = date(2026, 1, 1)
    suneung_date = today + timedelta(days=3)  # 수능이 코앞 - 이미 버퍼 기간 안 들어와 있음
    num_weeks = compute_num_weeks(today, today, target_weeks=None, suneung_date=suneung_date)
    assert num_weeks == 1


def test_quiz_score_to_grade_thresholds():
    assert quiz_score_to_grade(95).name == "Easy"
    assert quiz_score_to_grade(75).name == "Good"
    assert quiz_score_to_grade(55).name == "Hard"
    assert quiz_score_to_grade(20).name == "Again"


def test_review_lesson_works_cold_start():
    card, due = review_lesson(None, 80)  # 카드 없음 = 콜드스타트
    assert card is not None
    assert due is not None


def test_review_lesson_caps_interval_at_max_interval_days():
    """계속 고득점(Easy)을 받아도 다음 복습일이 max_interval_days를 넘지 않아야 함
    (py-fsrs 기본 maximum_interval=36500일은 수능일 전 앱에서 너무 관대함)."""
    card = None
    review_dt = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for _ in range(15):
        card, due = review_lesson(card, 95, review_datetime=review_dt, max_interval_days=10)
        assert (due - review_dt).days <= 10
        review_dt = due  # 실제 due일에 맞춰 다음 리뷰 진행 (연속 최고점)


def test_risk_ranks_personas_correctly():
    high = compute_rule_based_risk(recency_days=10, miss_streak_days=5)
    low = compute_rule_based_risk(recency_days=0, miss_streak_days=0)
    assert high > low
    assert risk_label(high) == "HIGH"
    assert risk_label(low) == "LOW"


def test_risk_falls_back_to_two_factor_without_quiz_score():
    two_factor = compute_rule_based_risk(recency_days=5, miss_streak_days=3)
    explicit_none = compute_rule_based_risk(recency_days=5, miss_streak_days=3, quiz_avg_score_percent=None)
    assert two_factor == explicit_none


def test_risk_increases_when_quiz_avg_score_is_low():
    same_activity_low_quiz = compute_rule_based_risk(recency_days=5, miss_streak_days=3, quiz_avg_score_percent=40)
    same_activity_high_quiz = compute_rule_based_risk(recency_days=5, miss_streak_days=3, quiz_avg_score_percent=95)
    assert same_activity_low_quiz > same_activity_high_quiz


def test_slip_status_triggers_push_at_one_week_threshold():
    assert compute_slip_status(cumulative_slip_minutes=100, weekly_average_minutes=300) == "on_track"
    assert compute_slip_status(cumulative_slip_minutes=300, weekly_average_minutes=300) == "push_mode"
    assert compute_slip_status(cumulative_slip_minutes=500, weekly_average_minutes=0) == "on_track"  # 콜드스타트


def test_slip_status_forces_push_mode_within_final_stretch_regardless_of_slip():
    # 밀림이 전혀 없어도(잘 따라오는 학생) 수능 D-100 이내면 무조건 push_mode
    assert compute_slip_status(cumulative_slip_minutes=0, weekly_average_minutes=300, days_until_suneung=100) == "push_mode"
    assert compute_slip_status(cumulative_slip_minutes=0, weekly_average_minutes=300, days_until_suneung=101) == "on_track"


def test_redistribute_uses_higher_multiplier_in_final_stretch():
    # daily_cap=30 -> 평소 push 배율(1.5)은 cap 45, 최종스퍼트(1.8)는 cap 54.
    # 16분짜리 강의를 누적하면 45는 2개(32)에서 멈추고 54는 3개(48)까지 들어가 배율 차이가 드러남.
    lessons = [{"id": f"l{i}", "duration_min": 16} for i in range(6)]
    normal_push = redistribute_remaining_week(lessons, remaining_days=3, status="push_mode", daily_cap_min=30, days_until_suneung=200)
    final_stretch_push = redistribute_remaining_week(lessons, remaining_days=3, status="push_mode", daily_cap_min=30, days_until_suneung=50)

    normal_day0_count = sum(1 for d in normal_push.values() if d == 0)
    final_day0_count = sum(1 for d in final_stretch_push.values() if d == 0)
    assert final_day0_count > normal_day0_count  # 최종스퍼트 배율이 더 높아서 첫날에 더 많이 몰림


def test_redistribute_spreads_evenly_when_on_track():
    lessons = [{"id": "a", "duration_min": 30}, {"id": "b", "duration_min": 30}, {"id": "c", "duration_min": 30}]
    result = redistribute_remaining_week(lessons, remaining_days=3, status="on_track", daily_cap_min=30)
    assert set(result.values()) == {0, 1, 2}  # 하루 하나씩 고르게


def test_redistribute_front_loads_when_push_mode():
    lessons = [
        {"id": "a", "duration_min": 20}, {"id": "b", "duration_min": 20},
        {"id": "c", "duration_min": 20}, {"id": "d", "duration_min": 20},
    ]
    on_track = redistribute_remaining_week(lessons, remaining_days=4, status="on_track", daily_cap_min=30)
    push = redistribute_remaining_week(lessons, remaining_days=4, status="push_mode", daily_cap_min=30)

    on_track_day0_count = sum(1 for d in on_track.values() if d == 0)
    push_day0_count = sum(1 for d in push.values() if d == 0)
    assert push_day0_count > on_track_day0_count  # push_mode가 첫날에 더 많이 몰아넣음(최대강도)


def test_assign_variant_is_deterministic_per_member():
    # 같은 학생은 몇 번을 호출해도 항상 같은 그룹이어야 함(sticky) - 안 그러면 실험이 무의미해짐
    v1 = assign_variant("member_1", "efficiency_stretch_factor", [0.3, 0.5, 0.7])
    v2 = assign_variant("member_1", "efficiency_stretch_factor", [0.3, 0.5, 0.7])
    assert v1 == v2
    assert v1 in [0.3, 0.5, 0.7]


def test_assign_variant_differs_across_experiments_for_same_member():
    # 같은 학생이라도 실험이 다르면 독립적으로 배정돼야 함(한 그룹으로만 계속 쏠리면 안 됨)
    variants = [0, 1, 2, 3, 4, 5, 6, 7]
    assignments = {
        exp: assign_variant("member_1", exp, variants)
        for exp in [f"experiment_{i}" for i in range(10)]
    }
    assert len(set(assignments.values())) > 1  # 전부 같은 값으로 쏠리지 않음


def test_assign_variant_raises_on_empty_variants():
    try:
        assign_variant("member_1", "exp", [])
        assert False, "ValueError를 기대했지만 발생하지 않음"
    except ValueError:
        pass

