"""주간 강의 배정 스케줄러 (OR-Tools CP-SAT).

x[강의, 주] = BoolVar (배정 여부)
- AddAtMostOne: 강의당 한 주만 배정
- weekly_cap 제약: 주별 합계 <= 그 주 용량
- 마감일 제약: 마감 이후 주는 배정변수 자체를 배제
- 선수관계: 주(선수강의) <= 주(후속강의)
"""
from ortools.sat.python import cp_model


def generate_weekly_schedule(lessons, weekly_caps, prerequisites=None):
    """
    lessons: [{"id": str, "duration_min": int, "deadline_week": int|None}]
    weekly_caps: [int] — 주차별 용량(분), index 0 = 이번 주
    prerequisites: [(선수강의id, 후속강의id)]
    반환: {lesson_id: week_index} 또는 배정 실패 시 None
    """
    model = cp_model.CpModel()
    num_weeks = len(weekly_caps)
    lesson_ids = [l["id"] for l in lessons]

    # x[lesson_id][week] = BoolVar
    x = {}
    for lesson in lessons:
        deadline = lesson.get("deadline_week")
        max_week = deadline if deadline is not None else num_weeks - 1
        for w in range(num_weeks):
            if w <= max_week:
                x[(lesson["id"], w)] = model.NewBoolVar(f"x_{lesson['id']}_{w}")
        # 강의당 정확히 한 주 배정 (배정 불가능하면 infeasible로 드러남)
        model.AddExactlyOne(x[(lesson["id"], w)] for w in range(num_weeks) if (lesson["id"], w) in x)

    # 주별 용량 제약
    for w in range(num_weeks):
        terms = []
        for lesson in lessons:
            if (lesson["id"], w) in x:
                terms.append(lesson["duration_min"] * x[(lesson["id"], w)])
        if terms:
            model.Add(sum(terms) <= weekly_caps[w])

    # 선수관계: 선수강의 주 <= 후속강의 주
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
    solver.parameters.num_search_workers = 4  # 소규모 문제라 4개면 충분(전체코어보다 빠름)
    status = solver.Solve(model)

    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return None  # infeasible — 상위 로직에서 "완주 불가" 경고 처리

    return {
        lesson_id: w
        for lesson_id in lesson_ids
        for w in range(num_weeks)
        if (lesson_id, w) in x and solver.Value(x[(lesson_id, w)]) == 1
    }
