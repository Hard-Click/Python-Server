"""복습 카드 갱신 크론 진입점 - 새 퀴즈 점수가 생긴 FSRS 카드의 상태/다음 복습일을 갱신.
크론탭에서 이 파일만 직접 실행: python -m presentation.jobs.review_update

이 배치가 review_card.due 의 생산자다. 여기서 갱신된 due 를 Java 스케줄 read(GET /api/schedule/me,
/me/today)가 읽어 '복습' 항목으로 노출하고, 프론트가 courseId 로 유사퀴즈 화면에 진입한다.
"""
from application.use_cases import ReviewLessonUseCase, UpdateDueReviewsUseCase
from infrastructure.repositories import (
    MySQLReviewCardRepository, MySQLQuizScoreRepository,
    MySQLSubscriptionRepository, MySQLPendingReviewRepository,
)
from infrastructure.error_router_client import ErrorRouterNotifier

notifier = ErrorRouterNotifier()

MAX_REPORTED_FAILURES = 10


def run():
    review_use_case = ReviewLessonUseCase(
        MySQLReviewCardRepository(), MySQLQuizScoreRepository(), MySQLSubscriptionRepository(),
    )
    use_case = UpdateDueReviewsUseCase(MySQLPendingReviewRepository(), review_use_case)

    try:
        report = use_case.execute()
    except Exception as e:  # noqa: BLE001 - 배치 시작 자체가 안 되는 상황(DB 다운, 대상 조회 불가 등)
        notifier.notify_failure("복습 카드 갱신 전체 실패 (대상 조회 불가)", str(e))
        raise

    failures = report["failures"]
    print(f"[review_update] targets={report['targets']} updated={report['updated']} "
          f"skipped={report['skipped']} failed={len(failures)}")

    if failures:
        detail = "\n".join(
            f"- enrollment_id={eid}, lesson_id={lid}: {err}"
            for eid, lid, err in failures[:MAX_REPORTED_FAILURES])
        if len(failures) > MAX_REPORTED_FAILURES:
            detail += f"\n...외 {len(failures) - MAX_REPORTED_FAILURES}건 더"
        notifier.notify_failure(
            f"복습 카드 갱신 일부 실패 ({len(failures)}/{report['targets']}건)", detail)


if __name__ == "__main__":
    run()
