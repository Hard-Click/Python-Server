"""이탈위험 규칙기반 스코어 (콜드스타트용, 순수 도메인 로직).

이탈 이벤트 데이터가 쌓이면 이 함수는 infrastructure의 CoxPHFitter 기반
구현으로 교체된다(application 레이어에서 어느 쪽을 쓸지 조합).
"""
from dataclasses import dataclass

# 가중치 - scripts/coxph_synthetic_population.py의 synthetic Cox PH 실험 ground truth
# 비율(recency 0.9 : streak 0.6 : quiz 0.4)을 정규화한 값. 3축(퀴즈 응시)과 2축(콜드스타트) 분리.
W_RECENCY_3AXIS = 0.45
W_STREAK_3AXIS = 0.30
W_QUIZ_3AXIS = 0.25
W_RECENCY_2AXIS = 0.5
W_STREAK_2AXIS = 0.5

# 축 코드 -> 화면(학생 위험 상세) '기여 요인' 라벨. recency=장기 미접속, streak=진도 밀림, quiz=퀴즈 점수 하락.
RISK_FACTOR_LABELS = {
    "recency": "장기 미접속",
    "streak": "진도 밀림",
    "quiz": "퀴즈 점수 하락",
}


@dataclass
class RiskBreakdown:
    """규칙기반 이탈위험 총점 + 축별 기여도 분해.

    화면의 '위험 점수 기여 요인' 막대(+38/+31/+19)를 그리려면 총점 하나론 부족 —
    각 축이 총점에 실제로 더한 절대량을 함께 반환한다. 정규화 가중치라 contributions 합 == score.
    """
    score: float                     # 0~1 총점
    label: str                       # HIGH/MEDIUM/LOW
    contributions: dict[str, float]  # {"recency","streak","quiz"} 축별 기여량(0~1), 합=score
    top_reason: str                  # 최대 기여 축 코드(대시보드 '사유' 표기용)


def compute_risk_breakdown(
    recency_days: int,
    miss_streak_days: int,
    quiz_avg_score_percent: float | None = None,
) -> RiskBreakdown:
    """0~1 총점을 축별 기여도까지 분해해 반환.

    quiz_avg_score_percent가 없으면(퀴즈 미응시 콜드스타트) recency+스트릭 2축,
    있으면 퀴즈점수를 3번째 축으로 추가. 가중치는 위 모듈 상수 참고.
    """
    recency_score = min(recency_days / 14, 1.0)
    streak_score = min(miss_streak_days / 7, 1.0)

    if quiz_avg_score_percent is None:
        contributions = {
            "recency": round(W_RECENCY_2AXIS * recency_score, 3),
            "streak": round(W_STREAK_2AXIS * streak_score, 3),
        }
        raw_score = W_RECENCY_2AXIS * recency_score + W_STREAK_2AXIS * streak_score
    else:
        quiz_risk_score = min(max(1 - quiz_avg_score_percent / 100, 0.0), 1.0)
        contributions = {
            "recency": round(W_RECENCY_3AXIS * recency_score, 3),
            "streak": round(W_STREAK_3AXIS * streak_score, 3),
            "quiz": round(W_QUIZ_3AXIS * quiz_risk_score, 3),
        }
        raw_score = (
            W_RECENCY_3AXIS * recency_score
            + W_STREAK_3AXIS * streak_score
            + W_QUIZ_3AXIS * quiz_risk_score
        )

    # 총점은 raw 합을 한 번만 round - compute_rule_based_risk 기존 반환값과 동일하게 유지.
    score = round(raw_score, 3)
    top_reason = max(contributions, key=contributions.get)
    return RiskBreakdown(
        score=score,
        label=risk_label(score),
        contributions=contributions,
        top_reason=top_reason,
    )


def compute_rule_based_risk(
    recency_days: int,
    miss_streak_days: int,
    quiz_avg_score_percent: float | None = None,
) -> float:
    """0~1 총점만 필요할 때(스크립트/캘리브레이션 등)의 편의 함수 - breakdown에 위임."""
    return compute_risk_breakdown(recency_days, miss_streak_days, quiz_avg_score_percent).score


def risk_label(score: float) -> str:
    if score >= 0.7:
        return "HIGH"
    if score >= 0.4:
        return "MEDIUM"
    return "LOW"
