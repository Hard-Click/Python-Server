"""UpdateDueReviewsUseCase - 대상조회/카드갱신을 fake로 두고 배치 오케스트레이션만 검증(DB 불필요).

핵심 계약:
  - 대상 목록만큼 ReviewLessonUseCase 를 돌린다
  - due=None(퀴즈 점수 없음)은 skipped 로 세고 실패가 아니다
  - 한 카드가 터져도 나머지는 계속 처리한다(배치 격리) - 실패는 요약에 모아 알림용으로 돌려준다
"""
from application.use_cases import UpdateDueReviewsUseCase


class FakePendingRepo:
    def __init__(self, targets):
        self._targets = targets

    def find_review_targets(self):
        return self._targets


class FakeReviewUseCase:
    """(enrollment_id, lesson_id) -> due 또는 raise 할 Exception."""

    def __init__(self, behavior):
        self._behavior = behavior
        self.calls = []

    def execute(self, enrollment_id, lesson_id):
        self.calls.append((enrollment_id, lesson_id))
        result = self._behavior.get((enrollment_id, lesson_id))
        if isinstance(result, Exception):
            raise result
        return result


def test_updates_every_target():
    pending = FakePendingRepo([("e1", "l1"), ("e1", "l2")])
    review = FakeReviewUseCase({("e1", "l1"): "2026-07-25", ("e1", "l2"): "2026-07-28"})

    report = UpdateDueReviewsUseCase(pending, review).execute()

    assert review.calls == [("e1", "l1"), ("e1", "l2")]
    assert report["targets"] == 2
    assert report["updated"] == 2
    assert report["skipped"] == 0
    assert report["failures"] == []


def test_none_due_counts_as_skipped_not_failure():
    # 퀴즈 점수가 사라진 경계 케이스 - ReviewLessonUseCase 가 None 을 돌려준다
    pending = FakePendingRepo([("e1", "l1")])
    review = FakeReviewUseCase({("e1", "l1"): None})

    report = UpdateDueReviewsUseCase(pending, review).execute()

    assert report["updated"] == 0
    assert report["skipped"] == 1
    assert report["failures"] == []


def test_one_failure_does_not_stop_the_batch():
    pending = FakePendingRepo([("e1", "l1"), ("e2", "l2"), ("e3", "l3")])
    review = FakeReviewUseCase({
        ("e1", "l1"): "2026-07-25",
        ("e2", "l2"): RuntimeError("카드 저장 실패"),
        ("e3", "l3"): "2026-07-30",
    })

    report = UpdateDueReviewsUseCase(pending, review).execute()

    # 터진 뒤에도 세 번째까지 호출됐는지 - 격리의 핵심
    assert review.calls == [("e1", "l1"), ("e2", "l2"), ("e3", "l3")]
    assert report["updated"] == 2
    assert report["failures"] == [("e2", "l2", "카드 저장 실패")]


def test_no_targets_is_a_clean_noop():
    review = FakeReviewUseCase({})

    report = UpdateDueReviewsUseCase(FakePendingRepo([]), review).execute()

    assert review.calls == []
    assert report == {"targets": 0, "updated": 0, "skipped": 0, "failures": []}
