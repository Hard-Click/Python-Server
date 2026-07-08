"""퀴즈점수 -> FSRS grade 매핑 + 복습카드 갱신 (순수 도메인 로직).

py-fsrs가 전역 기본가중치를 내장하고 있어 콜드스타트(개인 리뷰 0개)에도
바로 동작한다. 개인 리뷰가 쌓이면 fsrs-optimizer로 재학습한 가중치를
Scheduler(parameters=...)에 넣어주면 됨(로드맵, infrastructure에서 주입).
"""
from fsrs import Scheduler, Card, Rating


def quiz_score_to_grade(score_percent: float) -> Rating:
    """확정된 임계값: 90+=Easy, 70~89=Good, 50~69=Hard, <50=Again"""
    if score_percent >= 90:
        return Rating.Easy
    if score_percent >= 70:
        return Rating.Good
    if score_percent >= 50:
        return Rating.Hard
    return Rating.Again


def review_lesson(card: Card | None, quiz_score_percent: float, scheduler: Scheduler | None = None):
    """
    card: 기존 FSRS 카드 상태 (None이면 신규 생성 = 콜드스타트)
    반환: (갱신된 card, 다음 복습 예정일)
    """
    scheduler = scheduler or Scheduler()
    card = card or Card()
    rating = quiz_score_to_grade(quiz_score_percent)
    card, _review_log = scheduler.review_card(card, rating)
    return card, card.due
