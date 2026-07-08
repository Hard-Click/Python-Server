"""퀴즈점수 -> FSRS grade 매핑 + 복습카드 갱신.

py-fsrs가 전역 기본가중치를 내장하고 있어 콜드스타트(개인 리뷰 0개)에도
바로 동작한다. 개인 리뷰가 쌓이면 fsrs-optimizer로 가중치를 재학습해
Scheduler(parameters=...)에 개인 가중치를 넣어주면 됨(로드맵).
"""
from datetime import datetime, timezone
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
    card: 기존 FSRS 카드 상태 (없으면 신규 생성 = 콜드스타트)
    quiz_score_percent: 0~100
    반환: (갱신된 card, 다음 복습 예정일)
    """
    scheduler = scheduler or Scheduler()  # 전역 기본가중치
    card = card or Card()
    rating = quiz_score_to_grade(quiz_score_percent)
    card, _review_log = scheduler.review_card(card, rating)
    return card, card.due


if __name__ == "__main__":
    # 콜드스타트 시연: 카드 없이 퀴즈 3번 (한취약 페르소나 시뮬레이션 - 수학 도형 취약)
    card = None
    for score in [42, 55, 68]:
        card, due = review_lesson(card, score)
        print(f"점수={score} -> grade={quiz_score_to_grade(score).name}, "
              f"stability={card.stability:.2f}, 다음복습={due.date()}")
