"""야간 크론 진입점 - 매일 밤 이번 주 남은 날짜만 재조정 (Frozen Zone 준수).
크론탭: python -m presentation.jobs.nightly_reflow
"""
from application.use_cases import NightlyReflowUseCase
from infrastructure.repositories import MySQLWeeklyProgressRepository, MySQLSubscriptionRepository
from infrastructure.error_router_client import ErrorRouterNotifier
from infrastructure.db import get_connection

notifier = ErrorRouterNotifier()


def get_active_enrollment_ids() -> list[str]:
    sql = "SELECT id FROM enrollment WHERE status = 'active'"
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(sql)
        return [row["id"] for row in cur.fetchall()]


def run():
    use_case = NightlyReflowUseCase(MySQLWeeklyProgressRepository(), MySQLSubscriptionRepository())

    try:
        enrollment_ids = get_active_enrollment_ids()
    except Exception as e:  # noqa: BLE001 - 배치 시작 자체가 안 되는 치명적 상황(DB 다운 등)
        notifier.notify_failure("야간 리플로우 전체 실패 (활성 수강 목록 조회 불가)", str(e))
        raise

    failures = []  # 한 학생 실패가 나머지를 막지 않도록 격리
    for enrollment_id in enrollment_ids:
        try:
            result = use_case.execute(enrollment_id)
            print(f"[nightly_reflow] {enrollment_id}: {result['status']}")
        except Exception as e:  # noqa: BLE001
            failures.append((enrollment_id, str(e)))
            print(f"[nightly_reflow] {enrollment_id}: FAILED - {e}")

    if failures:
        detail = "\n".join(f"- enrollment_id={eid}: {err}" for eid, err in failures[:10])
        if len(failures) > 10:
            detail += f"\n...외 {len(failures) - 10}건 더"
        notifier.notify_failure(f"야간 리플로우 일부 실패 ({len(failures)}/{len(enrollment_ids)}건)", detail)


if __name__ == "__main__":
    run()
