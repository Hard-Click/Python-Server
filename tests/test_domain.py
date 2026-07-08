"""domain/ 순수 로직만 검증 - DB/네트워크 전혀 필요 없음."""
from domain.scheduler import generate_weekly_schedule, split_weekly_budget_by_grades
from domain.review import quiz_score_to_grade, review_lesson
from domain.risk import compute_rule_based_risk, risk_label
from domain.reflow import compute_slip_status, redistribute_remaining_week
from domain.result import Status
from domain.errors import SchedulerInputError, FsrsComputationError


def test_scheduler_respects_prerequisites_deadline_cap():
    lessons = [
        {"id": "math_1", "duration_min": 60, "deadline_week": 3},
        {"id": "math_2", "duration_min": 60, "deadline_week": 3},
        {"id": "math_3", "duration_min": 90, "deadline_week": 3},
    ]
    prerequisites = [("math_1", "math_2"), ("math_2", "math_3")]
    result = generate_weekly_schedule(lessons, [180, 180, 180, 180], prerequisites)
    assert result.status == Status.OK
    assignment = result.data["assignment"]
    assert assignment["math_1"] <= assignment["math_2"] <= assignment["math_3"]


def test_scheduler_pushes_back_when_cap_too_tight():
    lessons = [{"id": "a", "duration_min": 60, "deadline_week": 3}]
    result = generate_weekly_schedule(lessons, [10, 180, 180, 180])
    assert result.status == Status.OK
    assert result.data["assignment"]["a"] != 0  # 이번 주 cap이 부족하니 뒤로 밀림


def test_scheduler_raises_on_contract_violation():
    import pytest
    with pytest.raises(SchedulerInputError):
        generate_weekly_schedule([{"id": "a", "duration_min": -1}], [180])
    with pytest.raises(SchedulerInputError):
        generate_weekly_schedule([{"id": "a", "duration_min": 10}], [])


def test_scheduler_returns_infeasible_not_error_when_truly_unsolvable():
    # deadline_week=0으로 강제 + 그 주 cap이 부족 -> 어디로도 못 밀림 = 진짜 못 풂(에러 아님)
    lessons = [{"id": "a", "duration_min": 100, "deadline_week": 0}]
    result = generate_weekly_schedule(lessons, [10])
    assert result.status == Status.INFEASIBLE
    assert result.reason


def test_budget_split_favors_lower_grade_course():
    result = split_weekly_budget_by_grades(90, {"math": 3, "eng": 6})
    assert result["eng"] > result["math"]  # 등급 숫자 클수록(성적 안좋을수록) 더 배정


def test_quiz_score_to_grade_thresholds():
    assert quiz_score_to_grade(95).name == "Easy"
    assert quiz_score_to_grade(75).name == "Good"
    assert quiz_score_to_grade(55).name == "Hard"
    assert quiz_score_to_grade(20).name == "Again"


def test_review_lesson_signals_cold_start_status():
    result = review_lesson(None, 80)  # 카드 없음 = 콜드스타트
    assert result.status == Status.COLD_START
    assert result.data["card"] is not None
    assert result.data["due"] is not None


def test_review_lesson_returns_ok_when_card_exists():
    first = review_lesson(None, 80)
    second = review_lesson(first.data["card"], 85)
    assert second.status == Status.OK


def test_review_lesson_raises_on_invalid_score():
    import pytest
    with pytest.raises(FsrsComputationError):
        review_lesson(None, 150)
    with pytest.raises(FsrsComputationError):
        review_lesson(None, -1)


def test_risk_ranks_personas_correctly():
    high = compute_rule_based_risk(recency_days=10, miss_streak_days=5)
    low = compute_rule_based_risk(recency_days=0, miss_streak_days=0)
    assert high > low
    assert risk_label(high) == "HIGH"
    assert risk_label(low) == "LOW"


def test_slip_status_triggers_push_at_one_week_threshold():
    assert compute_slip_status(cumulative_slip_minutes=100, weekly_average_minutes=300) == "on_track"
    assert compute_slip_status(cumulative_slip_minutes=300, weekly_average_minutes=300) == "push_mode"
    assert compute_slip_status(cumulative_slip_minutes=500, weekly_average_minutes=0) == "on_track"  # 콜드스타트


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

