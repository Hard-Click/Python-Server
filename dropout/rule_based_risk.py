"""이탈위험 규칙기반 스코어 (콜드스타트용, Cox PH 데이터 쌓이기 전 폴백).

MOOC 이탈예측 문헌상 최강 예측변수 2개: recency(마지막 활동 후 경과일) +
연속 미달성 스트릭. 이후 이탈 이벤트가 충분히 쌓이면 lifelines의
CoxPHFitter로 승급(hazard ratio = 설명가능성 축).
"""


def compute_rule_based_risk(recency_days: int, miss_streak_days: int) -> float:
    """0~1 스코어. 단순 가중합 — 실측 쌓이면 Cox PH 계수로 대체될 임시값."""
    recency_score = min(recency_days / 14, 1.0)       # 14일 이상 미접속 = 최대치
    streak_score = min(miss_streak_days / 7, 1.0)      # 7일 연속 미달성 = 최대치
    return round(0.5 * recency_score + 0.5 * streak_score, 3)


def risk_label(score: float) -> str:
    if score >= 0.7:
        return "HIGH"
    if score >= 0.4:
        return "MEDIUM"
    return "LOW"


if __name__ == "__main__":
    # 정위험 페르소나 시뮬레이션: 접속 뜸해지고 스트릭 계속 끊김
    cases = [
        ("정위험(위험군)", 10, 5),
        ("박모범(우등생)", 0, 0),
        ("최밀림(밀림중)", 2, 3),
    ]
    for name, recency, streak in cases:
        score = compute_rule_based_risk(recency, streak)
        print(f"{name}: recency={recency}일, streak={streak}일 -> risk={score} ({risk_label(score)})")
