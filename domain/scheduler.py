"""주간 강의 배정 도메인 로직 (OR-Tools CP-SAT).

⚠️ 이 파일은 DB·네트워크·프레임워크를 절대 import하지 않는다.
   외부 데이터는 전부 파라미터로 받고, 결과는 순수 값으로 반환한다.
"""
import math
from datetime import date, timedelta

from ortools.sat.python import cp_model

# 하드 상한 슬립 버퍼(학생이 정한 기간에서 최대 며칠 더 밀리는 걸 허용할지) - 확정안.
SLIP_BUFFER_WEEKS = 2
# 수능 직전 새 진도를 완전히 멈추고 총복습/모의고사만 남기는 기간 - 확정안(2026-07-09).
REVIEW_BUFFER_WEEKS = 3


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


def compute_num_weeks(
    today: date,
    enrolled_at: date,
    target_weeks: int | None,
    suneung_date: date | None,
) -> int:
    """이 코스에 CP-SAT이 배정을 고려할 주차 수(하드 상한 로직).

    하드 상한 = min(학생이 정한 기간 + SLIP_BUFFER_WEEKS, 수능일 - REVIEW_BUFFER_WEEKS).
    target_weeks가 없으면(온보딩 미완료 콜드스타트) 수능일-버퍼까지 전부 사용.
    suneung_date가 없으면(구독 미확인) target_weeks 기준만 사용.
    둘 다 없으면 최소 1주(폴백 - 상위에서 별도 경고 처리 권장).
    """
    candidates = []
    if target_weeks is not None:
        candidates.append(enrolled_at + timedelta(weeks=target_weeks + SLIP_BUFFER_WEEKS))
    if suneung_date is not None:
        candidates.append(suneung_date - timedelta(weeks=REVIEW_BUFFER_WEEKS))

    if not candidates:
        return 1

    hard_cap_date = min(candidates)
    days_remaining = (hard_cap_date - today).days
    return max(1, math.ceil(days_remaining / 7))


def compute_required_extension_weeks(course_totals, total_weekly_minutes, max_extension_weeks):
    """통합 CP-SAT이 INFEASIBLE일 때, 실제로 몇 주를 더 늘려야 풀릴 가능성이 있는지 역산.

    course_totals: [{"total_duration_min","deadline_week"}] - 코스별 남은 총 분량과
        (확장 전) 마감 주차. 각 코스가 그 마감까지 확보된 전체 캡(total_weekly_minutes*(deadline_week+1))을
        "혼자 다 쓴다고 가정한 최선의 경우"에도 못 채우는 만큼(shortfall)을 보고 최소 필요 주수를 구함.
        (다른 코스와 캡을 나눠 써야 하는 실제 상황에서는 이보다 더 필요할 수 있음 - 그래서 재풀이로 검증함.)
    max_extension_weeks: 하드캡(SLIP_BUFFER_WEEKS) - 이걸 넘는 필요치가 나오면 그냥 그 값으로 클램핑해서
        반환하고, 호출부가 "그래도 안 되면 물리적으로 불가능"으로 처리하게 한다.
    반환: 필요한 확장 주수(0이면 이 추정으로는 확장이 무의미 - 다른 원인일 가능성).
    """
    needed = 0
    for course in course_totals:
        available = total_weekly_minutes * (course["deadline_week"] + 1)
        shortfall = course["total_duration_min"] - available
        if shortfall > 0:
            needed = max(needed, math.ceil(shortfall / total_weekly_minutes))
    return min(needed, max_extension_weeks)


MIN_EFFICIENCY_SAMPLES = 3  # 이 미만이면 콜드스타트 취급(1.0, 강사 추정치 그대로)
MIN_EFFICIENCY_COEFFICIENT = 0.5  # 극단치 클램핑(완료 몇 건만 보고 절반 이하로 확신하면 안 됨)
MAX_EFFICIENCY_COEFFICIENT = 2.0
# 실측 페이스를 그대로 믿지 않고 1.0(강사 추정치) 쪽으로 절반만 당기는 "스트레치 목표"
# - 확정안(2026-07-09). 실측을 100% 그대로 반영하면 딴짓하느라 오래 걸린 학생도 시스템이
#   "원래 느리다"고 순응해버려서 일정이 계속 루즈해지는 자기강화 루프가 생김.
# 이 값 자체가 검증 안 된 정책값(A/B 테스트 필요, docs/policy_constants.md 참고)이라
# compute_efficiency_coefficient()가 override 가능하게 열어둠 - application 레이어가
# domain/experiments.py로 학생마다 다른 후보값을 배정해서 실험할 수 있게.
EFFICIENCY_STRETCH_FACTOR = 0.5


def compute_efficiency_coefficient(completed_lessons, stretch_factor: float = EFFICIENCY_STRETCH_FACTOR) -> float:
    """학생의 실제 학습속도 대 강사 추정치 비율. CP-SAT의 duration_min을 이걸로
    스케일링해야 "그 학생 기준" 최적화가 된다 - 안 하면 며칠이 지나도 강사가 잡은
    고정 추정치로만 계산돼서 개인화가 전혀 안 일어난다(day1부터 지금까지 실제로 이 상태였음).

    completed_lessons: [{"expected_duration_min","actual_duration_min"}] - 완료된 강의 기록.
    표본이 MIN_EFFICIENCY_SAMPLES 미만이면 콜드스타트로 보고 1.0(강사 추정치 그대로) 반환.
    합계 비율(sum(actual)/sum(expected))을 raw로 구하되, 최종값은 raw를 그대로 쓰지 않고
    1.0 쪽으로 stretch_factor만큼 당긴 값을 쓴다(예: raw=1.5, factor=0.5면 최종 1.25) -
    저효율 학생을 살짝 더 빠른 페이스로 유도하는 스트레치 목표. 실측이 실제로 개선되면
    (raw가 낮아지면) 목표도 같이 낮아져 따라가므로 무리한 고정 목표는 아니다.
    그래도 최종값은 0.5~2.0으로 클램핑(표본이 적을 때 과신 방지).
    """
    if len(completed_lessons) < MIN_EFFICIENCY_SAMPLES:
        return 1.0

    total_expected = sum(l["expected_duration_min"] for l in completed_lessons)
    total_actual = sum(l["actual_duration_min"] for l in completed_lessons)
    if total_expected <= 0:
        return 1.0

    raw_coefficient = total_actual / total_expected
    stretched_coefficient = 1.0 + stretch_factor * (raw_coefficient - 1.0)
    return min(max(stretched_coefficient, MIN_EFFICIENCY_COEFFICIENT), MAX_EFFICIENCY_COEFFICIENT)


def generate_unified_weekly_schedule(lessons, weekly_caps, prerequisites=None, grade_weights=None, course_weekly_caps=None):
    """다중코스 통합 CP-SAT.

    코스별로 주간예산을 등급 비율로 미리 쪼개고 그 안에서 각자 독립적으로 풀던
    기존 2단계 방식은 "코스마다 마감까지 남은 주차수가 다르다"는 걸 무시해서,
    등급이 같아도 마감이 급한 코스가 예산을 충분히 못 받아 INFEASIBLE이 나는
    문제가 있었다(예: 등급 동일한데 한쪽은 4주, 한쪽은 10주 남은 경우).
    이 함수는 그 대신 전체 코스의 강의를 하나의 모델에 넣고, 학생의 실제
    주간 가용시간(코스가 공유)만으로 공동 최적화한다 - 마감이 급한 코스는
    deadline_week 제약으로 자동으로 우선 배정된다(별도 예산 사전분배 불필요).

    lessons: [{"id","course_id","duration_min","deadline_week"}]
        deadline_week은 호출부가 "그 강의가 속한 코스의 하드 상한 주차"로 미리 세팅해서 넘겨야 함
        (코스마다 num_weeks가 다르므로 - compute_num_weeks() 결과를 그대로 쓰면 됨).
    weekly_caps: [int] - 학생 전체가 모든 코스에 공유하는 주간 가용시간, index 0 = 이번 주
    prerequisites: [(pre_id, post_id)] - 전체 코스 통합(강의 id가 전역 고유해야 함)
    grade_weights: {course_id: 등급(1~9)} - 등급 나쁠수록(숫자 클수록) 그 코스 강의를
        더 이른 주차에 배정하도록 소프트 선호(목적함수). 하드 제약은 아님 - feasibility가 우선.
    course_weekly_caps: {course_id: 주간 상한(분)} - 강사가 코스 등록 시 정한 '코스별 강도
        상한(하루 최대 학습 시간)'을 학습일수로 환산한 주간 상한. 전체 주간 캡(weekly_caps)과
        별개의 하드 제약으로, 특정 주에 한 과목이 과몰리는 것을 막는다(과목 균형). 없으면 미적용.
        None/빈 dict면 코스별 제약 없음(기존 동작과 동일).
    반환: {lesson_id: week_index} 또는 배정 실패(=전체 코스 통틀어 완주 불가) 시 None
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

    # 코스별 주간 상한(강사 입력 '하루 최대 학습 시간' × 학습일수). 전체 주간 캡과 별개로 걸어서
    # 한 과목이 특정 주에 몰리는 것을 막는다. 여기서 INFEASIBLE이 나면 호출부(_solve_with_extension)의
    # 연장 재시도 → 최종적으로 "완주 불가" 알림 경로를 그대로 탄다(별도 처리 불필요).
    if course_weekly_caps:
        for w in range(num_weeks):
            for course_id, cap in course_weekly_caps.items():
                course_terms = [
                    lesson["duration_min"] * x[(lesson["id"], w)]
                    for lesson in lessons
                    if lesson.get("course_id") == course_id and (lesson["id"], w) in x
                ]
                if course_terms:
                    model.Add(sum(course_terms) <= cap)

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

    if grade_weights:
        objective_terms = [
            grade_weights[lesson["course_id"]] * w * x[(lesson["id"], w)]
            for lesson in lessons
            for w in range(num_weeks)
            if (lesson["id"], w) in x and grade_weights.get(lesson["course_id"], 0) > 0
        ]
        if objective_terms:
            model.Minimize(sum(objective_terms))

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
