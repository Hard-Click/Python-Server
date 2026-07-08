"""domain/ 순수 로직만 검증 - DB/네트워크 전혀 필요 없음."""
from domain.scheduler import generate_weekly_schedule, split_weekly_budget_by_grades
from domain.review import quiz_score_to_grade, review_lesson
from domain.risk import compute_rule_based_risk, risk_label


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


def test_budget_split_favors_lower_grade_course():
    result = split_weekly_budget_by_grades(90, {"math": 3, "eng": 6})
    assert result["eng"] > result["math"]  # 등급 숫자 클수록(성적 안좋을수록) 더 배정


def test_quiz_score_to_grade_thresholds():
    assert quiz_score_to_grade(95).name == "Easy"
    assert quiz_score_to_grade(75).name == "Good"
    assert quiz_score_to_grade(55).name == "Hard"
    assert quiz_score_to_grade(20).name == "Again"


def test_review_lesson_works_cold_start():
    card, due = review_lesson(None, 80)  # 카드 없음 = 콜드스타트
    assert card is not None
    assert due is not None


def test_risk_ranks_personas_correctly():
    high = compute_rule_based_risk(recency_days=10, miss_streak_days=5)
    low = compute_rule_based_risk(recency_days=0, miss_streak_days=0)
    assert high > low
    assert risk_label(high) == "HIGH"
    assert risk_label(low) == "LOW"
