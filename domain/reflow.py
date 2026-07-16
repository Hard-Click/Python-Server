"""야간 미세조정 도메인 로직 (G정책: 매일 밤 누적 판정, 확정안 (a)).

Frozen Zone 원칙: 이 함수는 "이번 주 남은 날짜"만 대상으로 한다.
이미 지나간 날짜/오늘 확정분은 여기서 절대 건드리지 않는다(호출하는 쪽이 보장).
"""

# 극한푸시 시 하루 상한 배율. 관리자 전역정책값 후보(현재는 상수, 나중에 admin 설정으로 승격 가능).
PUSH_CAP_MULTIPLIER = 1.5

# 수능 D-100 이내엔 개인 슬립 여부와 무관하게 전반적으로 강도를 올린다(확정안, 2026-07-09).
FINAL_STRETCH_DAYS = 100
FINAL_STRETCH_PUSH_CAP_MULTIPLIER = 1.8  # 평소 push_mode(1.5배)보다 더 세게


def compute_slip_status(
    cumulative_slip_minutes: int,
    weekly_average_minutes: int,
    days_until_suneung: int | None = None,
) -> str:
    """누적 밀림량이 그 학생의 주간평균 학습량을 넘으면 push_mode.
    weekly_average_minutes가 0이면(데이터 없음, 콜드스타트) on_track 취급.
    days_until_suneung이 FINAL_STRETCH_DAYS 이내면 개인 슬립 여부와 무관하게
    무조건 push_mode(수능 직전 전반적 강도 상향 - 잘 따라오고 있는 학생도 예외 없음)."""
    if days_until_suneung is not None and days_until_suneung <= FINAL_STRETCH_DAYS:
        return "push_mode"
    if weekly_average_minutes <= 0:
        return "on_track"
    return "push_mode" if cumulative_slip_minutes >= weekly_average_minutes else "on_track"


def redistribute_remaining_week(
    remaining_lessons,
    remaining_days: int,
    status: str,
    daily_cap_min: int,
    days_until_suneung: int | None = None,
) -> dict:
    """
    remaining_lessons: [{"id": str, "duration_min": int}] - 이번 주 아직 안 끝낸 것들
    remaining_days: 이번 주 남은 날 수(오늘 이후, 오늘 포함 여부는 호출부에서 결정)
    status: compute_slip_status() 결과
    days_until_suneung: FINAL_STRETCH_DAYS 이내면 push_mode 배율을 더 올림(전반적 강도 상향)
    반환: {lesson_id: day_offset}  (day_offset 0 = 남은 날짜 중 첫날)

    on_track: 하루 배정량이 daily_cap을 넘지 않게 고르게 분산.
    push_mode: 앞쪽 날짜부터 (daily_cap * 배율)까지 채우고 넘치면 다음날로 - 최대강도 되감기.
    """
    if remaining_days <= 0 or not remaining_lessons:
        return {}

    if status != "push_mode":
        cap = daily_cap_min
    elif days_until_suneung is not None and days_until_suneung <= FINAL_STRETCH_DAYS:
        cap = daily_cap_min * FINAL_STRETCH_PUSH_CAP_MULTIPLIER
    else:
        cap = daily_cap_min * PUSH_CAP_MULTIPLIER

    assignment = {}
    day = 0
    day_used = 0
    for lesson in remaining_lessons:
        # 하루 용량 초과하면 다음날로 - 마지막 날에서 넘치면 마지막 날에 몰아넣음(완주불가 여부는 상위 CP-SAT/경고 로직 담당)
        if day_used + lesson["duration_min"] > cap and day < remaining_days - 1:
            day += 1
            day_used = 0
        assignment[lesson["id"]] = day
        day_used += lesson["duration_min"]

    return assignment
