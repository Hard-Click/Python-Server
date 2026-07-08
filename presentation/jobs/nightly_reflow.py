"""야간 크론 진입점 - 매일 밤 이번 주 남은 날짜만 재조정 (Frozen Zone 준수).
크론탭: python -m presentation.jobs.nightly_reflow
"""
from application.use_cases import NightlyReflowUseCase
from infrastructure.repositories import MySQLWeeklyProgressRepository
from infrastructure.error_router_client import ErrorRouterNotifier
from infrastructure.db import get_connection

notifier = ErrorRouterNotifier()


def get_active_enrollment_ids() -> list[str]:
    sql = "SELECT id FROM enrollment WHERE status = 'active'"
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(sql)
        return [row["id"] for row in cur.fetchall()]


def run():
    use_case = NightlyReflowUseCase(MySQLWeeklyProgressRepository())
    try:
        for enrollment_id in get_active_enrollment_ids():
            result = use_case.execute(enrollment_id)
            print(f"[nightly_reflow] {enrollment_id}: {result['status']}")
    except Exception as e:  # noqa: BLE001 - 배치는 실패해도 죽지 않고 알림만 보냄
        notifier.notify_failure("야간 리플로우 실패", str(e))
        raise


if __name__ == "__main__":
    run()
