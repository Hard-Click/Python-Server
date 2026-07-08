"""유스케이스 - domain 로직과 repository(포트)를 엮어서 실제 흐름을 수행.

DB가 뭔지(MySQL인지), 어떻게 조회하는지는 여기서 전혀 모른다 - 포트만 호출한다.
"""
from domain.scheduler import generate_weekly_schedule, split_weekly_budget_by_grades
from domain.review import review_lesson
from domain.risk import compute_rule_based_risk, risk_label
from domain.reflow import compute_slip_status, redistribute_remaining_week
from application.ports import (
    LessonRepository, DiagnosticScoreRepository, ScheduleRepository,
    ReviewCardRepository, QuizScoreRepository, ActivityRepository, RiskRepository,
    WeeklyProgressRepository,
)


class GenerateWeeklyScheduleUseCase:
    """학생 1명의 활성 코스 전체를 대상으로 주간 스케줄 생성 (다중코스 cap 분배 포함)."""

    def __init__(
        self,
        lesson_repo: LessonRepository,
        diagnostic_repo: DiagnosticScoreRepository,
        schedule_repo: ScheduleRepository,
    ):
        self.lesson_repo = lesson_repo
        self.diagnostic_repo = diagnostic_repo
        self.schedule_repo = schedule_repo

    def execute(self, member_id: str, enrollments: list[dict], total_weekly_minutes: int, num_weeks: int = 4):
        """enrollments: [{"enrollment_id","course_id"}]"""
        course_ids = [e["course_id"] for e in enrollments]
        grades = self.diagnostic_repo.get_grades_for_student(member_id, course_ids)
        budget_by_course = split_weekly_budget_by_grades(total_weekly_minutes, grades)

        results = {}
        for enrollment in enrollments:
            course_id = enrollment["course_id"]
            lessons = self.lesson_repo.get_lessons_for_course(course_id)
            prerequisites = self.lesson_repo.get_prerequisites(course_id)
            course_weekly_cap = budget_by_course.get(course_id, total_weekly_minutes // len(enrollments))
            weekly_caps = [course_weekly_cap] * num_weeks

            assignment = generate_weekly_schedule(lessons, weekly_caps, prerequisites)
            if assignment is None:
                results[enrollment["enrollment_id"]] = {"status": "INFEASIBLE"}
                continue

            self.schedule_repo.save_weekly_schedule(enrollment["enrollment_id"], 0, assignment)
            results[enrollment["enrollment_id"]] = {"status": "OK", "assignment": assignment}
        return results


class ReviewLessonUseCase:
    def __init__(self, card_repo: ReviewCardRepository, quiz_repo: QuizScoreRepository):
        self.card_repo = card_repo
        self.quiz_repo = quiz_repo

    def execute(self, enrollment_id: str, lesson_id: str):
        score = self.quiz_repo.get_latest_quiz_score(enrollment_id, lesson_id)
        if score is None:
            return None  # 퀴즈 미응시 - 복습 스케줄링 대상 아님
        card = self.card_repo.get_card(enrollment_id, lesson_id)
        new_card, due = review_lesson(card, score)
        self.card_repo.save_card(enrollment_id, lesson_id, new_card)
        return due


class ComputeRiskUseCase:
    def __init__(self, activity_repo: ActivityRepository, risk_repo: RiskRepository):
        self.activity_repo = activity_repo
        self.risk_repo = risk_repo

    def execute(self, enrollment_id: str):
        recency, streak = self.activity_repo.get_recency_and_streak(enrollment_id)
        score = compute_rule_based_risk(recency, streak)
        label = risk_label(score)
        self.risk_repo.save_risk_score(enrollment_id, score, label)
        return score, label


class NightlyReflowUseCase:
    """G정책 (a) 확정안: 매일 밤 누적 밀림량 판정 + 이번 주 남은 날짜만 재분배.
    Frozen Zone: 이 유스케이스는 '남은 날짜'만 건드리고 지나간 날짜/오늘 확정분은 손대지 않는다."""

    def __init__(self, progress_repo: WeeklyProgressRepository):
        self.progress_repo = progress_repo

    def execute(self, enrollment_id: str):
        slip = self.progress_repo.get_cumulative_slip_minutes(enrollment_id)
        weekly_avg = self.progress_repo.get_weekly_average_minutes(enrollment_id)
        status = compute_slip_status(slip, weekly_avg)

        remaining_lessons = self.progress_repo.get_remaining_lessons_this_week(enrollment_id)
        remaining_days = self.progress_repo.get_remaining_days_this_week(enrollment_id)
        daily_cap = self.progress_repo.get_daily_cap_minutes(enrollment_id)

        assignment = redistribute_remaining_week(remaining_lessons, remaining_days, status, daily_cap)
        self.progress_repo.save_day_assignment(enrollment_id, assignment)
        return {"status": status, "assignment": assignment}
