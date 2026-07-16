"""Shadow 파이프라인 dry-run - 실사용자·DB 없이 shadow mode 전체 경로를 합성 학생으로 돌려본다.

실제 GenerateWeeklyScheduleUseCase를 shadow mode 그대로 합성 모집단에 실행 → in-memory fake
repo가 shadow 로그를 수집 → SummarizeShadowDecisionsUseCase로 집계 리포트 출력. 즉 use case →
shadow 계산 → 로깅 → 집계까지 '진짜 코드 경로'를 통합 점검한다(CP-SAT 포함, DB만 fake).

★ 이게 증명하는 것: 파이프라인 배선·로깅·집계가 실제로 동작한다(통합 테스트).
★ 이게 증명 못 하는 것: shadow mode의 진짜 값어치(real traffic). 합성으로 돌리면 Phase 0~2가
  준 '합성 정책 변화량'을 재생산할 뿐, 새 증거는 없다 - 잠재변수는 여전히 실사용자만 가른다.
  발표에선 "실배포 전에 파이프라인이 돈다는 걸 이렇게 확인했다"까지만 주장할 것.

실행: python -m scripts.dryrun_shadow_pipeline
"""
import sys

import numpy as np

from application.use_cases import (
    GenerateWeeklyScheduleUseCase, SummarizeShadowDecisionsUseCase,
    EFFICIENCY_STRETCH_EXPERIMENT_NAME, SHADOW_MODE,
)
from domain.shadow_report import format_summary_lines

N_MEMBERS = 60
FAR_SUNEUNG_WEEKS = 40


def build_synthetic_members(rng):
    """관측 가능한 속성만으로 다양한 학생 생성(빠름/느림/과부하/콜드스타트 섞음)."""
    members = {}
    for i in range(N_MEMBERS):
        mid = f"dry_m{i}"
        cid = f"{mid}_c"
        raw = rng.uniform(1.1, 1.7) if rng.random() < 0.7 else rng.uniform(0.8, 1.0)
        completed = int(rng.integers(0, 3)) if rng.random() < 0.15 else 5
        base_total = rng.choice([1800, 3600, 6000])
        n_lessons = max(1, round(base_total / 60))
        members[mid] = {
            "course_id": cid,
            "enrollment_id": f"{mid}_e",
            "raw": float(raw),
            "completed": completed,
            "n_lessons": n_lessons,
            "target_weeks": int(rng.choice([6, 8, 10])),
            "cap": int(rng.choice([300, 420, 500])),
        }
    return members


class _FakeLessonRepo:
    def __init__(self, members):
        self._by_course = {m["course_id"]: m["n_lessons"] for m in members.values()}

    def get_lessons_for_course(self, course_id):
        return [{"id": f"{course_id}_L{k}", "duration_min": 60} for k in range(self._by_course[course_id])]

    def get_prerequisites(self, course_id):
        return []


class _FakeDiagnosticRepo:
    def get_grades_for_student(self, member_id, course_ids):
        return {c: 3 for c in course_ids}


class _FakeScheduleRepo:
    def save_weekly_schedule(self, enrollment_id, week, assignment):
        pass


class _FakeSubscriptionRepo:
    def __init__(self, today):
        from datetime import timedelta
        self._suneung = today + timedelta(weeks=FAR_SUNEUNG_WEEKS)

    def get_suneung_date(self, enrollment_id):
        return self._suneung


class _FakeLessonProgressRepo:
    def __init__(self, members):
        self._members = members

    def get_completed_lesson_durations(self, member_id):
        m = self._members[member_id]
        return [{"course_id": m["course_id"], "expected_duration_min": 100,
                 "actual_duration_min": 100 * m["raw"]}] * m["completed"]


class _FakeNotificationRepo:
    def notify_schedule_infeasible(self, member_id):
        pass

    def notify_schedule_extended(self, member_id, weeks):
        pass


class _FakeExperimentRepo:
    """in-memory: log만 모으고 get_shadow_decisions로 그대로 돌려준다(DB 대역)."""
    def __init__(self):
        self.shadow = []

    def log_exposure(self, member_id, experiment_name, variant):
        pass

    def log_shadow_decision(self, member_id, experiment_name, decision):
        self.shadow.append(dict(decision))

    def get_shadow_decisions(self, experiment_name):
        return self.shadow


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    from datetime import date
    if not SHADOW_MODE:
        print("SHADOW_MODE=False라 dry-run 의미 없음(적용값=variant). use_cases.py에서 켜고 실행할 것.")
        return

    rng = np.random.default_rng(42)
    members = build_synthetic_members(rng)
    exp = _FakeExperimentRepo()
    use_case = GenerateWeeklyScheduleUseCase(
        _FakeLessonRepo(members), _FakeDiagnosticRepo(), _FakeScheduleRepo(),
        _FakeSubscriptionRepo(date.today()), _FakeLessonProgressRepo(members),
        _FakeNotificationRepo(), exp,
    )

    print(f"합성 학생 {len(members)}명에게 실제 use case를 shadow mode로 실행 중...\n")
    for mid, m in members.items():
        enrollments = [{
            "enrollment_id": m["enrollment_id"], "course_id": m["course_id"],
            "enrolled_at": date.today(), "target_weeks": m["target_weeks"],
        }]
        use_case.execute(mid, enrollments, total_weekly_minutes=m["cap"])

    summary = SummarizeShadowDecisionsUseCase(exp).execute(EFFICIENCY_STRETCH_EXPERIMENT_NAME)
    print("\n".join(format_summary_lines(summary)))
    print("\n※ dry-run: 파이프라인(계산→로깅→집계)이 돈다는 통합 점검일 뿐, 성과/실측 증거 아님.")


if __name__ == "__main__":
    main()
