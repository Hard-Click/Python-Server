"""MySQLScheduleRepository._spread_lessons_over_week 회귀 테스트.

기존 버그: save_weekly_schedule가 같은 주(week_offset)에 배정된 강의를 전부
그 주 첫날(오늘 + week_offset*7일) 하루에 꽂았다(요일 분산 없음). 온보딩 직후
라이브로 스케줄을 생성하면 강의 10개가 전부 당일에 뭉쳐 보이는 문제로 실측됨.

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


def test_overflow_piles_into_last_study_day():
    # 학습 가능일이 이틀뿐인데 강의가 3개(각 60분, 상한 60분) -> 마지막 날에 몰아넣는다(완주불가 여부는 상위 로직 몫)
    rest_days = 0b1111100  # 일/월만 학습, 나머지 쉬는 날
    day_of_week = MySQLScheduleRepository._spread_lessons_over_week(
        _durations(60, 60, 60), rest_days=rest_days, daily_cap_min=60,
    )
    assert len(set(day_of_week.values())) == 2, "학습 가능일 수를 넘는 요일에 배정하면 안 된다"


def test_no_rest_day_info_falls_back_to_all_seven_days():
    # rest_days=None(온보딩 전) -> 매일 학습 가능한 것으로 간주(뭉치는 것보다 낫다)
    day_of_week = MySQLScheduleRepository._spread_lessons_over_week(
        _durations(20, 20), rest_days=None, daily_cap_min=20,
    )
    assert day_of_week["lesson-0"] != day_of_week["lesson-1"]
