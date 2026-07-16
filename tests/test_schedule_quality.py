"""domain/schedule_quality.py 순수 테스트 (DB 불필요)."""
from domain.schedule_quality import evaluate_schedule


def _lesson(lid, dur, course, dl):
    return {"id": lid, "duration_min": dur, "course_id": course, "deadline_week": dl}


def test_even_spread_has_low_cv_and_no_overload():
    lessons = [_lesson(f"L{i}", 100, "c1", 3) for i in range(4)]
    assignment = {"L0": 0, "L1": 1, "L2": 2, "L3": 3}  # 주당 100분 고르게
    m = evaluate_schedule(assignment, lessons, weekly_cap=420, num_weeks=4)
    assert m["mean_weekly_load"] == 100.0
    assert m["load_cv"] == 0.0            # 완전 균등
    assert m["overloaded_weeks"] == 0
    assert m["max_consecutive_overload_weeks"] == 0


def test_detects_consecutive_overload():
    lessons = [_lesson(f"L{i}", 500, "c1", 3) for i in range(3)]
    assignment = {"L0": 0, "L1": 1, "L2": 3}  # 0,1주 연속 과부하(500>420), 3주도 과부하지만 끊김
    m = evaluate_schedule(assignment, lessons, weekly_cap=420, num_weeks=4)
    assert m["overloaded_weeks"] == 3
    assert m["max_consecutive_overload_weeks"] == 2


def test_subject_concentration_and_slack():
    lessons = [_lesson("A0", 100, "math", 2), _lesson("A1", 100, "math", 2), _lesson("B0", 100, "eng", 3)]
    assignment = {"A0": 0, "A1": 0, "B0": 1}  # 0주는 math만(편중 1.0), math 마지막=0주(마감2 -> slack2)
    m = evaluate_schedule(assignment, lessons, weekly_cap=420, num_weeks=4)
    assert m["peak_subject_share"] == 1.0
    assert m["min_deadline_slack_weeks"] == 2   # eng: dl3-week1=2, math: dl2-week0=2 -> min 2


def test_negative_slack_when_scheduled_past_deadline():
    lessons = [_lesson("A0", 100, "math", 1)]
    assignment = {"A0": 3}  # 마감 1주인데 3주에 배정 -> slack -2
    m = evaluate_schedule(assignment, lessons, weekly_cap=420, num_weeks=4)
    assert m["min_deadline_slack_weeks"] == -2
