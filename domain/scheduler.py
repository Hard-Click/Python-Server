"""주간 강의 배정 도메인 로직 (OR-Tools CP-SAT).

⚠️ 이 파일은 DB·네트워크·프레임워크를 절대 import하지 않는다.
   외부 데이터는 전부 파라미터로 받고, 결과는 순수 값으로 반환한다.
"""
from ortools.sat.python import cp_model


def generate_weekly_schedule(lessons, weekly_caps, prerequisites=None):
    """
    lessons: [{"id": str, "duration_min": int, "deadline_week": int|None}]
    weekly_caps: [int] — 주차별 용량(분), index 0 = 이번 주
    prerequisites: [(선수강의id, 후속강의id)]
    반환: {lesson_id: week_index} 또는 배정 실패 시 None(=완주 불가)
    """
    model = cp_model.CpModel()
    num_weeks = len(weekly_caps)
    lesson_ids = [l["id"] for l in lessons]

    x = {}
    for lesson in lessons:
        deadline = lesson.get("deadline_week")
        max_week = deadline if deadline is not None else num_weeks - 1
        for w in range(num_weeks):
            if w <= max_week:
                x[(lesson["id"], w)] = model.NewBoolVar(f"x_{lesson['id']}_{w}")
        model.AddExactlyOne(x[(lesson["id"], w)] for w in range(num_weeks) if (lesson["id"], w) in x)

    for w in range(num_weeks):
        terms = [
            lesson["duration_min"] * x[(lesson["id"], w)]
            for lesson in lessons
            if (lesson["id"], w) in x
        ]
        if terms:
            model.Add(sum(terms) <= weekly_caps[w])

    if prerequisites:
        week_of = {}
        for lesson in lessons:
            week_of[lesson["id"]] = model.NewIntVar(0, num_weeks - 1, f"week_{lesson['id']}")
            model.Add(
                week_of[lesson["id"]]
                == sum(w * x[(lesson["id"], w)] for w in range(num_weeks) if (lesson["id"], w) in x)
            )
        for pre_id, post_id in prerequisites:
            if pre_id in week_of and post_id in week_of:
                model.Add(week_of[pre_id] <= week_of[post_id])

    solver = cp_model.CpSolver()
    solver.parameters.num_search_workers = 4
    status = solver.Solve(model)

    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return None

    return {
        lesson_id: w
        for lesson_id in lesson_ids
        for w in range(num_weeks)
        if (lesson_id, w) in x and solver.Value(x[(lesson_id, w)]) == 1
    }


def split_weekly_budget_by_grades(total_minutes: int, grades: dict) -> dict:
    """다중코스 cap 예산 분배. grades: {course_id: 등급(1~9)} — 등급 나쁠수록(숫자 클수록) 더 배정.
    모의고사 없는 과목은 grades에서 빠지며, 그 경우 남은 예산을 균등분배(콜드스타트 폴백)."""
    if not grades:
        return {}
    total_grade = sum(grades.values())
    if total_grade == 0:
        even = total_minutes // len(grades)
        return {course_id: even for course_id in grades}
    return {
        course_id: round(total_minutes * grade / total_grade)
        for course_id, grade in grades.items()
    }
