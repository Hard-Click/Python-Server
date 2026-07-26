"""MySQLScheduleRepository._spread_lessons_over_week 회귀 테스트.

기존 버그 1: save_weekly_schedule가 같은 주(week_offset)에 배정된 강의를 전부
그 주 첫날(오늘 + week_offset*7일) 하루에 꽂았다(요일 분산 없음). 온보딩 직후
라이브로 스케줄을 생성하면 강의 10개가 전부 당일에 뭉쳐 보이는 문제로 실측됨.

기존 버그 2(1차 수정 후에도 남아있던 것): "하루 상한을 넘기지 않는 한 최대한 압축해서
채우는" 방식이라, 하루 상한이 넉넉하면(예: 480분) 강의 5개(총 200분)가 전부 하루에
몰렸다 - 라이브 재현으로 실측됨. 분산학습(spaced practice)이 집중학습보다 기억 정착에
유리하다는 원리에 반하므로, 압축이 아니라 "학습 가능일에 고르게 분산"을 기본으로 바꿈.
하루 상한은 하드 상한 가드로만 남긴다.

_spread_lessons_over_week는 DB에 안 붙는 순수 로직이라 DB 없이 검증 가능하다.
"""
from infrastructure.repositories import MySQLScheduleRepository


def _durations(*mins):
    return [(f"lesson-{i}", m) for i, m in enumerate(mins)]


def test_spreads_across_available_study_days_when_under_cap():
    # 쉬는날 없음(rest_days=0), 하루 상한 40분, 강의 3개 x 40분 -> 서로 다른 날로 흩어져야 함
    day_of_week = MySQLScheduleRepository._spread_lessons_over_week(
        _durations(40, 40, 40), rest_days=0, daily_cap_min=40,
    )
    assigned_days = list(day_of_week.values())
    assert len(set(assigned_days)) == 3, "하루 상한을 넘기지 않는 강의들이 같은 날에 뭉치면 안 된다"
    assert assigned_days == sorted(assigned_days), "요일은 순서대로 채워져야 한다(0,1,2,...)"


def test_respects_rest_days_bitmask():
    # bit0=일요일 쉬는날 -> 일요일(day 0)은 배정 대상에서 빠져야 한다
    rest_days = 0b0000001  # 일요일만 쉬는 날
    day_of_week = MySQLScheduleRepository._spread_lessons_over_week(
        _durations(30, 30), rest_days=rest_days, daily_cap_min=30,
    )
    assert 0 not in day_of_week.values(), "쉬는날(일요일)에는 강의가 배정되면 안 된다"


def test_overflow_piles_when_study_days_run_out():
    # 학습 가능일이 이틀뿐인데 강의가 3개(각 60분, 상한 60분) -> 존재하는 학습일 안에서만 배정한다
    # (완주 불가 여부는 상위 CP-SAT/알림 로직 몫이지, 없는 요일을 만들어내지 않는다)
    rest_days = 0b1111100  # 일/월만 학습, 나머지 쉬는 날
    day_of_week = MySQLScheduleRepository._spread_lessons_over_week(
        _durations(60, 60, 60), rest_days=rest_days, daily_cap_min=60,
    )
    assert len(set(day_of_week.values())) == 2, "학습 가능일 수를 넘는 요일에 배정하면 안 된다"


def test_light_load_still_spreads_across_days_even_with_abundant_capacity():
    # 실측된 회귀 케이스: 하루 상한이 넉넉하면(480분) 강의 5개(총 200분)가 전부 하루에
    # 들어갈 수 있다고 해서 그렇게 몰아넣으면 안 된다 - 분산학습 원칙상 학습 가능일에 걸쳐
    # 고르게 흩어져야 한다.
    day_of_week = MySQLScheduleRepository._spread_lessons_over_week(
        _durations(40, 40, 40, 40, 40), rest_days=0, daily_cap_min=480,
    )
    assert len(set(day_of_week.values())) == 5, "여유 있는 상한이어도 강의는 여러 날에 걸쳐 분산돼야 한다"


def test_no_rest_day_info_falls_back_to_all_seven_days():
    # rest_days=None(온보딩 전) -> 매일 학습 가능한 것으로 간주(뭉치는 것보다 낫다)
    day_of_week = MySQLScheduleRepository._spread_lessons_over_week(
        _durations(20, 20), rest_days=None, daily_cap_min=20,
    )
    assert day_of_week["lesson-0"] != day_of_week["lesson-1"]
