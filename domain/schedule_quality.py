"""스케줄 품질 평가 지표 (순수 로직 - DB/프레임워크 import 없음).

CP-SAT가 낸 배정(assignment)이 '풀렸다/안 풀렸다'를 넘어 얼마나 좋은지를 관측 가능한 지표로
정량화한다. 성과(점수) 지표가 아니라 스케줄 구조 품질(부하 고르기·편중·마감 여유·과부하 지속)이다.
두 배정을 비교(예: baseline vs shadow)하거나 관리자 대시보드/회귀 감시에 쓸 수 있다.

입력:
  assignment: {lesson_id: week_index}
  lessons: [{"id","duration_min","course_id","deadline_week"}]  (효율계수 적용 후 duration 권장)
  weekly_cap: 주간 가용 분
  num_weeks: 전체 주 수(>=1)
"""
import statistics


def evaluate_schedule(assignment, lessons, weekly_cap, num_weeks):
    num_weeks = max(1, num_weeks)
    by_id = {lesson["id"]: lesson for lesson in lessons}

    # 주차별 총 부하 / 주차·코스별 부하
    weekly_load = [0.0] * num_weeks
    weekly_course_load = [dict() for _ in range(num_weeks)]
    course_last_week = {}
    for lesson_id, week in assignment.items():
        lesson = by_id.get(lesson_id)
        if lesson is None or not (0 <= week < num_weeks):
            continue
        dur = lesson["duration_min"]
        weekly_load[week] += dur
        cid = lesson.get("course_id")
        weekly_course_load[week][cid] = weekly_course_load[week].get(cid, 0.0) + dur
        if lesson.get("deadline_week") is not None:
            course_last_week[cid] = max(course_last_week.get(cid, week), week)

    mean_load = sum(weekly_load) / num_weeks
    load_stdev = statistics.pstdev(weekly_load) if num_weeks > 1 else 0.0
    # 변동계수(CV): 부하가 주마다 얼마나 들쭉날쭉한지(0에 가까울수록 고르게 분산 = 좋음)
    load_cv = (load_stdev / mean_load) if mean_load > 0 else 0.0

    # 과부하: 가용 캡 초과 주 수 + 연속 과부하 최대 길이
    overloaded_weeks = sum(1 for load in weekly_load if load > weekly_cap)
    max_consecutive_overload = _max_run(load > weekly_cap for load in weekly_load)

    # 과목 편중도: 각 주에서 한 코스가 차지하는 최대 비중(1에 가까울수록 그 주가 한 과목에 쏠림)
    peak_subject_share = 0.0
    for week in range(num_weeks):
        total = weekly_load[week]
        if total > 0:
            peak_subject_share = max(peak_subject_share, max(weekly_course_load[week].values()) / total)

    # 마감 여유(slack): 코스별 (마감 주차 - 마지막 배정 주차). 음수면 마감 넘겨 배정(위험).
    slacks = {
        cid: by_id_deadline(by_id, cid) - last_week
        for cid, last_week in course_last_week.items()
    }
    min_deadline_slack = min(slacks.values()) if slacks else None

    return {
        "num_weeks": num_weeks,
        "mean_weekly_load": round(mean_load, 1),
        "load_stdev": round(load_stdev, 1),
        "load_cv": round(load_cv, 3),
        "peak_weekly_load": round(max(weekly_load), 1) if weekly_load else 0.0,
        "overloaded_weeks": overloaded_weeks,
        "max_consecutive_overload_weeks": max_consecutive_overload,
        "peak_subject_share": round(peak_subject_share, 3),
        "min_deadline_slack_weeks": min_deadline_slack,
    }


def by_id_deadline(by_id, course_id):
    """그 코스 소속 강의 중 하나의 deadline_week(코스 단위로 동일하다고 가정)."""
    for lesson in by_id.values():
        if lesson.get("course_id") == course_id and lesson.get("deadline_week") is not None:
            return lesson["deadline_week"]
    return 0


def _max_run(bool_iter):
    best = run = 0
    for flag in bool_iter:
        run = run + 1 if flag else 0
        best = max(best, run)
    return best
