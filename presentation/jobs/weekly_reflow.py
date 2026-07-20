"""주간 크론 진입점 - 전체 활성 enrollment를 순회하며 리플로우 실행.
크론탭에서 이 파일만 직접 실행: python -m presentation.jobs.weekly_reflow
"""
from application.use_cases import GenerateWeeklyScheduleUseCase, ComputeRiskUseCase
from infrastructure.repositories import (
    MySQLLessonRepository, MySQLDiagnosticScoreRepository, MySQLScheduleRepository,
    MySQLActivityRepository, MySQLRiskRepository, MySQLQuizScoreRepository,
    MySQLSubscriptionRepository, MySQLStudentCapRepository, MySQLLessonProgressRepository,
    MySQLStudentNotificationRepository, MySQLExperimentRepository,
    MySQLCourseLearningPolicyRepository,
)
from infrastructure.error_router_client import ErrorRouterNotifier
from infrastructure.db import get_connection

notifier = ErrorRouterNotifier()


def get_active_enrollments_by_student() -> dict:
    """{member_id: [{"enrollment_id","course_id","enrolled_at","target_weeks"}]}
    학생별로 묶어야 다중코스 cap 분배 가능. target_weeks는 nullable(온보딩 미완료)."""
    sql = """
        SELECT member_id, enrollment_id, course_id, enrolled_at, target_weeks
        FROM enrollment WHERE status = 'IN_PROGRESS'
    """
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(sql)
        rows = cur.fetchall()
    grouped = {}
    for row in rows:
        grouped.setdefault(row["member_id"], []).append({
            "enrollment_id": row["enrollment_id"],
            "course_id": row["course_id"],
            "enrolled_at": row["enrolled_at"],
            "target_weeks": row["target_weeks"],
        })
    return grouped


def run():
    schedule_use_case = GenerateWeeklyScheduleUseCase(
        MySQLLessonRepository(), MySQLDiagnosticScoreRepository(), MySQLScheduleRepository(),
        MySQLSubscriptionRepository(), MySQLLessonProgressRepository(),
        MySQLStudentNotificationRepository(), MySQLExperimentRepository(),
        course_policy_repo=MySQLCourseLearningPolicyRepository(),
    )
    risk_use_case = ComputeRiskUseCase(
        MySQLActivityRepository(), MySQLRiskRepository(), MySQLQuizScoreRepository(),
    )
    student_cap_repo = MySQLStudentCapRepository()

    try:
        by_student = get_active_enrollments_by_student()
    except Exception as e:  # noqa: BLE001 - 배치 시작 자체가 안 되는 치명적 상황(DB 다운 등)
        notifier.notify_failure("주간 리플로우 전체 실패 (활성 수강 목록 조회 불가)", str(e))
        raise

    failures = []  # 한 학생 실패가 나머지를 막지 않도록 격리
    for member_id, enrollments in by_student.items():
        try:
            total_weekly_minutes = student_cap_repo.get_weekly_available_minutes(member_id)
            study_days = student_cap_repo.get_study_days(member_id)
            schedule_use_case.execute(member_id, enrollments, total_weekly_minutes, study_days=study_days)
            for enrollment in enrollments:
                risk_use_case.execute(enrollment["enrollment_id"])
        except Exception as e:  # noqa: BLE001
            failures.append((member_id, str(e)))
            print(f"[weekly_reflow] member_id={member_id}: FAILED - {e}")

    if failures:
        detail = "\n".join(f"- member_id={mid}: {err}" for mid, err in failures[:10])
        if len(failures) > 10:
            detail += f"\n...외 {len(failures) - 10}건 더"
        notifier.notify_failure(f"주간 리플로우 일부 실패 ({len(failures)}/{len(by_student)}건)", detail)


if __name__ == "__main__":
    run()
