"""이탈위험 규칙기반 스코어 (콜드스타트용, 순수 도메인 로직).

이탈 이벤트 데이터가 쌓이면 이 함수는 infrastructure의 CoxPHFitter 기반
구현으로 교체된다(application 레이어에서 어느 쪽을 쓸지 조합).
"""


def compute_rule_based_risk(recency_days: int, miss_streak_days: int) -> float:
    """0~1 스코어. MOOC 이탈예측 최강 예측변수 2개(recency+스트릭) 가중합."""
    recency_score = min(recency_days / 14, 1.0)
    streak_score = min(miss_streak_days / 7, 1.0)
    return round(0.5 * recency_score + 0.5 * streak_score, 3)


def risk_label(score: float) -> str:
    if score >= 0.7:
        return "HIGH"
    if score >= 0.4:
        return "MEDIUM"
    return "LOW"
