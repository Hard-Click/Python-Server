"""Cox PH 학습용 synthetic population 생성 + 학습 스크립트.

목적: (1) 이탈위험을 규칙기반(domain/risk.py)에서 Cox PH로 승급했을 때 실제로
      계수가 안정적으로 잡히는 표본수가 얼마인지 감을 잡는다.
      (2) 현재 규칙기반 가중치(recency 0.5 / streak 0.5, domain/risk.py)가
      "참" 위험요인 구조와 방향이라도 맞는지 검증한다.

방법: 진짜 hazard를 우리가 정해서(ground truth) 학생을 합성 생성 → 생존시간
샘플링 → 관찰기간(censoring) 적용 → CoxPHFitter로 역으로 계수를 추정해
ground truth와 비교한다. DB/네트워크 없음 (domain/risk.py의 피처 정의만 재사용).

실행: python -m scripts.coxph_synthetic_population
"""
import sys

import numpy as np
import pandas as pd
from lifelines import CoxPHFitter
from lifelines.utils import concordance_index

from domain.risk import compute_rule_based_risk

OBSERVATION_WINDOW_DAYS = 90

# "참" 계수 (합성 데이터 생성용 ground truth) - recency/streak가 클수록 이탈(hazard) 증가,
# quiz_avg가 높을수록 이탈 감소. 표준화된 피처에 대한 log-hazard 계수.
TRUE_BETA = {
    "recency_days_z": 0.9,
    "miss_streak_days_z": 0.6,
    "quiz_avg_score_z": -0.4,
}


def generate_population(n, rng):
    recency_days = rng.exponential(scale=6, size=n).round().astype(int)
    miss_streak_days = rng.exponential(scale=4, size=n).round().astype(int)
    quiz_avg_score = np.clip(rng.normal(loc=75, scale=15, size=n), 0, 100)

    df = pd.DataFrame({
        "recency_days": recency_days,
        "miss_streak_days": miss_streak_days,
        "quiz_avg_score": quiz_avg_score,
    })

    for col in ["recency_days", "miss_streak_days", "quiz_avg_score"]:
        df[f"{col}_z"] = (df[col] - df[col].mean()) / df[col].std()

    log_hazard = sum(TRUE_BETA[f"{c}_z"] * df[f"{c}_z"] for c in ["recency_days", "miss_streak_days", "quiz_avg_score"])
    baseline_scale = 60  # 기본 생존시간 스케일(일)
    scale = baseline_scale * np.exp(-log_hazard)
    true_survival_days = rng.exponential(scale=scale)

    df["duration"] = np.minimum(true_survival_days, OBSERVATION_WINDOW_DAYS)
    df["event"] = (true_survival_days <= OBSERVATION_WINDOW_DAYS).astype(int)

    df["rule_based_risk_2factor"] = [
        compute_rule_based_risk(int(r), int(m))
        for r, m in zip(df["recency_days"], df["miss_streak_days"])
    ]
    df["rule_based_risk_3factor"] = [
        compute_rule_based_risk(int(r), int(m), float(q))
        for r, m, q in zip(df["recency_days"], df["miss_streak_days"], df["quiz_avg_score"])
    ]
    return df


def fit_and_compare(df):
    cph = CoxPHFitter()
    cph.fit(df[["duration", "event", "recency_days_z", "miss_streak_days_z", "quiz_avg_score_z"]],
            duration_col="duration", event_col="event")

    c_index_cox = concordance_index(df["duration"], -cph.predict_partial_hazard(df), df["event"])
    # 규칙기반 스코어는 "위험도"라서 생존시간과 반대 방향 -> concordance_index에는 그대로 위험도를 event_observed와 맞춰 음수 처리
    c_index_rule_2f = concordance_index(df["duration"], -df["rule_based_risk_2factor"], df["event"])
    c_index_rule_3f = concordance_index(df["duration"], -df["rule_based_risk_3factor"], df["event"])

    return cph, c_index_cox, c_index_rule_2f, c_index_rule_3f


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    rng = np.random.default_rng(42)

    print(f"관찰기간: {OBSERVATION_WINDOW_DAYS}일, ground truth 계수: {TRUE_BETA}\n")
    print(
        f"{'n':>5} {'events':>7} {'cox_recency':>12} {'cox_streak':>11} {'cox_quiz':>9} "
        f"{'c_index_cox':>12} {'c_idx_rule2f':>13} {'c_idx_rule3f':>13}"
    )

    for n in [30, 80, 150, 300]:
        df = generate_population(n, rng)
        n_events = int(df["event"].sum())
        if n_events < 5:
            print(f"{n:>5} {n_events:>7}  (이벤트 too few, skip fit)")
            continue
        cph, c_cox, c_rule_2f, c_rule_3f = fit_and_compare(df)
        coef = cph.params_
        print(
            f"{n:>5} {n_events:>7} {coef['recency_days_z']:>12.3f} {coef['miss_streak_days_z']:>11.3f} "
            f"{coef['quiz_avg_score_z']:>9.3f} {c_cox:>12.3f} {c_rule_2f:>13.3f} {c_rule_3f:>13.3f}"
        )

    print("\n(참고) ground truth 방향: recency +0.9(클수록 이탈), streak +0.6(클수록 이탈), quiz -0.4(높을수록 이탈 감소)")
    print("rule2f = 기존 recency+streak만, rule3f = 퀴즈점수 추가한 신규 공식(domain/risk.py)")


if __name__ == "__main__":
    main()
