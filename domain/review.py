"""퀴즈점수 -> FSRS grade 매핑 + 복습카드 갱신 (순수 도메인 로직).

py-fsrs가 전역 기본가중치를 내장하고 있어 콜드스타트(개인 리뷰 0개)에도
바로 동작한다. 개인 리뷰가 쌓이면 fsrs-optimizer로 재학습한 가중치를
Scheduler(parameters=...)에 넣어주면 됨(로드맵, infrastructure에서 주입).
"""
from fsrs import Scheduler, Card, Rating
from domain.result import AiResult, Status
from domain.errors import FsrsComputationError


def quiz_score_to_grade(score_percent: float) -> Rating:
    """확정된 임계값: 90+=Easy, 70~89=Good, 50~69=Hard, <50=Again"""
    if score_percent >= 90:
        return Rating.Easy
    if score_percent >= 70:
        return Rating.Good
    if score_percent >= 50:
        return Rating.Hard
    return Rating.Again


def review_lesson(card: Card | None, quiz_score_percent: float, scheduler: Scheduler | None = None) -> AiResult:
    """
    card: 기존 FSRS 카드 상태 (None이면 신규 생성 = 콜드스타트)
    반환: AiResult
      - OK: 기존 카드로 재학습, data={"card":card, "due":due}
      - COLD_START: card가 없어 전역 기본가중치로 신규 생성(에러 아님, 정상 흐름) - 같은 data 구조
    raise FsrsComputationError: quiz_score_percent가 0~100 범위 밖(호출측 계약 위반)
    """
    if not (0 <= quiz_score_percent <= 100):
        raise FsrsComputationError(f"quiz_score_percent는 0~100 범위여야 함: {quiz_score_percent}")

    is_cold_start = card is None
    scheduler = scheduler or Scheduler()
    card = card or Card()
    rating = quiz_score_to_grade(quiz_score_percent)
    card, _review_log = scheduler.review_card(card, rating)

    status = Status.COLD_START if is_cold_start else Status.OK
    return AiResult(status, data={"card": card, "due": card.due})
