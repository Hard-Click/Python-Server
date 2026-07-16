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


def review_lesson(
    card: Card | None,
    quiz_score_percent: float,
    scheduler: Scheduler | None = None,
    review_datetime=None,
    max_interval_days: int | None = None,
):
    """
    card: 기존 FSRS 카드 상태 (None이면 신규 생성 = 콜드스타트)
    review_datetime: 리뷰 발생 시각(UTC datetime). None이면 실제 현재시각 사용(운영 기본값).
        시뮬레이션/백테스트에서 여러 날에 걸친 리뷰를 재현하려면 명시적으로 넘길 것.
    max_interval_days: 다음 복습일까지 최대 며칠까지 늘어날 수 있는지 상한.
        py-fsrs 기본값(maximum_interval=36500일, 즉 100년)은 이 앱에서 너무 관대함 —
        고득점을 계속 받는 학생은 다음 복습이 수능일(구독 상한선)을 넘겨 잡힐 수 있음.
        scheduler를 직접 넘기면 이 값은 무시된다(호출부가 이미 캡을 설정했다고 간주).
    반환: (갱신된 card, 다음 복습 예정일)
    """
    if scheduler is None:
        scheduler = Scheduler(maximum_interval=max_interval_days) if max_interval_days is not None else Scheduler()
    card = card or Card()
    rating = quiz_score_to_grade(quiz_score_percent)
    card, _review_log = scheduler.review_card(card, rating, review_datetime=review_datetime)
    return card, card.due
