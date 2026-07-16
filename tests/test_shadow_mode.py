"""Shadow mode 안전성 테스트: 배정 variant가 실제 스케줄엔 절대 반영되지 않고(적용은 sf=0.0
baseline), 결정 델타는 log_shadow_decision으로만 관측된다는 것을 fake repo로 잠근다."""
from datetime import date, timedelta

from application.use_cases import (
    GenerateWeeklyScheduleUseCase, EFFICIENCY_STRETCH_VARIANTS, SHADOW_APPLIED_STRETCH_FACTOR,
)


class _FakeLessonRepo:
    def get_lessons_for_course(self, course_id):
        return [{"id": f"{course_id}_L{i}", "duration_min": 60} for i in range(3)]

    def get_prerequisites(self, course_id):
        return []


class _FakeDiagnosticRepo:
    def get_grades_for_student(self, member_id, course_ids):
        return {c: 3 for c in course_ids}


class _FakeScheduleRepo:
    def __init__(self):
        self.saved = []

    def save_weekly_schedule(self, enrollment_id, week, assignment):
        self.saved.append((enrollment_id, week, assignment))


class _FakeSubscriptionRepo:
    def get_suneung_date(self, enrollment_id):
        return date.today() + timedelta(weeks=40)


class _FakeLessonProgressRepo:
    """관측상 느린 학생: raw=1.5(actual/expected), 표본 3건 이상 -> coeff가 1.0에서 벗어난다."""
    def get_completed_lesson_durations(self, member_id):
        return [{"course_id": "c1", "expected_duration_min": 100, "actual_duration_min": 150}] * 5


class _FakeNotificationRepo:
    def __init__(self):
        self.infeasible = []
        self.extended = []

    def notify_schedule_infeasible(self, member_id):
        self.infeasible.append(member_id)

    def notify_schedule_extended(self, member_id, weeks):
        self.extended.append((member_id, weeks))


class _FakeExperimentRepo:
    def __init__(self):
        self.exposures = []
        self.shadow_decisions = []

    def log_exposure(self, member_id, experiment_name, variant):
        self.exposures.append((member_id, experiment_name, variant))

    def log_shadow_decision(self, member_id, experiment_name, decision):
        self.shadow_decisions.append((member_id, experiment_name, decision))


class _FakeCoursePolicyRepo:
    """강사가 코스 등록 시 정한 코스별 하루 최대 학습 분."""
    def __init__(self, daily_max):
        self._daily_max = daily_max  # {course_id: 분}

    def get_daily_max_minutes(self, course_ids):
        return {c: self._daily_max[c] for c in course_ids if c in self._daily_max}


def _make_use_case(course_policy_repo=None):
    exp = _FakeExperimentRepo()
    sched = _FakeScheduleRepo()
    uc = GenerateWeeklyScheduleUseCase(
        _FakeLessonRepo(), _FakeDiagnosticRepo(), sched, _FakeSubscriptionRepo(),
        _FakeLessonProgressRepo(), _FakeNotificationRepo(), exp,
        course_policy_repo=course_policy_repo,
    )
    return uc, exp, sched


def test_per_course_cap_spreads_lessons_when_policy_and_study_days_given():
    """강사가 정한 코스별 하루 상한(daily_max)이 study_days와 함께 주어지면, 전체 주간 캡이
    넉넉해도 한 과목이 한 주에 몰리지 않고 상한(daily_max×study_days) 안에서 분산돼야 한다.
    이게 '코스 등록 입력이 저장만 되고 스케줄러가 무시하던' 버그의 회귀 방지."""
    enrollments = [{"enrollment_id": "e1", "course_id": "c1", "enrolled_at": date.today(), "target_weeks": 8}]

    # 상한 없음(study_days 미전달): 3강(각 60분)이 한 주에 몰릴 수 있음
    uc0, _, _ = _make_use_case()
    r0 = uc0.execute("m1", enrollments, total_weekly_minutes=420)
    assert len(set(r0["e1"]["assignment"].values())) == 1

    # daily_max=60, study_days=1 -> 코스 주간상한 60분 -> 주당 1강 -> 3주로 분산
    uc1, _, _ = _make_use_case(_FakeCoursePolicyRepo({"c1": 60}))
    r1 = uc1.execute("m1", enrollments, total_weekly_minutes=420, study_days=1)
    assert len(set(r1["e1"]["assignment"].values())) == 3


def test_shadow_mode_applies_baseline_not_variant():
    """적용 스케줄은 sf=0.0(강사 추정치)로 만들어야 한다 - shadow 로그의 applied 요약이 그 증거."""
    uc, exp, sched = _make_use_case()
    enrollments = [{"enrollment_id": "e1", "course_id": "c1", "enrolled_at": date.today(), "target_weeks": 8}]

    results = uc.execute("member_1", enrollments, total_weekly_minutes=420)

    assert results["e1"]["status"] == "OK"
    assert sched.saved, "실제 스케줄이 저장되어야 함"
    assert len(exp.shadow_decisions) == 1
    _, _, decision = exp.shadow_decisions[0]
    # 적용은 baseline(sf=0.0) -> coeff 정확히 1.0
    assert decision["applied_stretch_factor"] == SHADOW_APPLIED_STRETCH_FACTOR
    assert decision["applied_coeff_mean"] == 1.0


def test_shadow_logs_variant_delta_without_touching_applied():
    """배정 variant는 log에만 남고, shadow가 applied보다 더 무겁다(raw>1, sf>0)는 델타가 잡혀야."""
    uc, exp, sched = _make_use_case()
    enrollments = [{"enrollment_id": "e1", "course_id": "c1", "enrolled_at": date.today(), "target_weeks": 8}]

    uc.execute("member_1", enrollments, total_weekly_minutes=420)

    _, _, decision = exp.shadow_decisions[0]
    assert decision["variant"] in EFFICIENCY_STRETCH_VARIANTS
    # 배정 variant를 적용했다면 coeff>1.0(느린 학생) -> shadow 총량이 applied보다 큼
    assert decision["shadow_coeff_mean"] > 1.0
    assert decision["shadow_total_min"] > decision["applied_total_min"]
    assert decision["weekly_minutes_delta"] > 0
    assert decision["schedule_would_change"] is True
    # exposure도 여전히 기록
    assert exp.exposures and exp.exposures[0][2] == decision["variant"]
    # 둘 다 완주 가능(콜드스타트 없는 이 케이스) -> 품질 델타가 실제로 계산돼 로그에 남아야 함
    assert decision["quality_delta"] is not None


def test_clock_is_injected_not_date_today():
    """주입한 clock이 실제로 호출되어야 한다(날짜 로직이 date.today() 직접호출이 아님을 잠금)."""
    calls = []

    def fixed_clock():
        calls.append(1)
        return date(2026, 1, 1)

    exp = _FakeExperimentRepo()
    uc = GenerateWeeklyScheduleUseCase(
        _FakeLessonRepo(), _FakeDiagnosticRepo(), _FakeScheduleRepo(), _FakeSubscriptionRepo(),
        _FakeLessonProgressRepo(), _FakeNotificationRepo(), exp, clock=fixed_clock,
    )
    enrollments = [{"enrollment_id": "e1", "course_id": "c1", "enrolled_at": date(2026, 1, 1), "target_weeks": 8}]

    uc.execute("member_1", enrollments, total_weekly_minutes=420)

    assert calls, "주입한 clock이 호출되지 않음 - date.today() 직접호출이 남아있다는 뜻"


def test_preview_commits_nothing():
    """commit=False(preview): 스케줄 저장·exposure·shadow 로그 어떤 부작용도 없어야 한다."""
    uc, exp, sched = _make_use_case()
    enrollments = [{"enrollment_id": "e1", "course_id": "c1", "enrolled_at": date.today(), "target_weeks": 8}]

    results = uc.execute("member_1", enrollments, total_weekly_minutes=420, commit=False)

    assert results["e1"]["status"] == "OK"        # 계산 결과는 정상 반환
    assert results["e1"]["assignment"]            # 스케줄은 계산됨
    assert sched.saved == []                      # 저장 안 함
    assert exp.exposures == []                    # exposure 안 남김
    assert exp.shadow_decisions == []             # shadow 로그 안 남김
