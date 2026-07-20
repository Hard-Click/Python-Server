"""domain/risk.py의 규칙기반 risk 가중치(0.45/0.30/0.25)를 실측 데이터로 재검증.

배경: 그 가중치는 scripts/coxph_synthetic_population.py의 synthetic ground truth를
정규화한 값이라 - 실측이 아니라 "그럴듯한 가정"일 뿐이다(docs/policy_constants.md 참고).
이 스크립트는 실제 이탈 이벤트가 쌓였을 때 Cox PH로 실측 계수를 뽑아 현재 상수와
비교하고, 드리프트가 크면 교체를 권고한다 - "규칙기반→Cox PH 승급" 로드맵의 실행 지점.

DB 연결(DB_HOST 등 환경변수)이 없거나 실측 데이터가 부족하면 synthetic 데이터로
폴백한다 - 이 경우 출력에 명확히 "SYNTHETIC FALLBACK"이라고 표시되니, 그 결과를
실제 가중치 교체 근거로 쓰면 안 된다(여전히 가정일 뿐).

실행: python -m scripts.calibrate_risk_weights
"""
import sys

import numpy as np
import pandas as pd
from lifelines import CoxPHFitter

from domain.risk import compute_rule_based_risk

# domain/risk.py에 하드코딩된 현재 값 - 여기 숫자를 바꾸면 domain/risk.py도 같이 바꿔야 함(단일 소스 아님, 주의).
CURRENT_WEIGHTS = {"recency": 0.45, "streak": 0.30, "quiz": 0.25}

# 이 이상 벌어지면 "드리프트 큼 - 교체 검토"로 표시(임의 임계값, POLICY 판단).
DRIFT_WARNING_THRESHOLD = 0.15

MIN_REAL_EVENTS_FOR_CALIBRATION = 30  # 이 미만이면 실측이어도 신뢰 안 함 - synthetic과 동일 취급


def load_real_population():
    """실제 RDS에서 이탈 이벤트 데이터를 가져온다(실 스키마 정합 2026-07-20).

    Cox 라벨(duration/event)은 daily_achievement 날짜에서 유도하지 않고 dropout_event를 직접 쓴다 -
    관측기간·중도절단(censored) 판정이 이미 그 테이블의 계약이기 때문. enrollment.status에는
    'dropped' 값이 없다(ENUM: COMPLETED/ENROLLED/EXPIRED/IN_PROGRESS/REFUNDED).

    각 지표는 상관 서브쿼리로 뽑는다 - daily_achievement와 quiz_submission을 같이 JOIN하면
    행이 곱해져서 SUM/AVG가 부풀려진다(옛 쿼리의 실제 버그).

    ⚠️ quiz_avg_score는 member 단위다. quiz_submission엔 enrollment_id가 없어 코스별로 못 쪼갠다 -
    다중 수강 학생은 타 코스 점수가 섞인다.
    """
    from infrastructure.db import get_connection

    sql = """
        SELECT
          DATEDIFF(CURDATE(), (
            SELECT MAX(da.achieved_date) FROM daily_achievement da
            WHERE da.enrollment_id = e.enrollment_id
          )) AS recency_days,
          (
            SELECT COUNT(*) FROM daily_achievement da
            WHERE da.enrollment_id = e.enrollment_id
              AND da.achieved = 0
              AND da.achieved_date > COALESCE((
                    SELECT MAX(da2.achieved_date) FROM daily_achievement da2
                    WHERE da2.enrollment_id = e.enrollment_id AND da2.achieved = 1
                  ), '1000-01-01')
          ) AS miss_streak_days,
          (
            SELECT AVG(qs.score) FROM quiz_submission qs
            WHERE qs.member_id = e.member_id
          ) AS quiz_avg_score,
          de.observed_days AS duration,
          de.event_occurred AS event
        FROM enrollment e
        JOIN dropout_event de ON de.enrollment_id = e.enrollment_id
    """
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(sql)
        rows = cur.fetchall()

    df = pd.DataFrame(rows)
    if df.empty:
        return df
    # CoxPHFitter는 NaN에서 죽는다. 퀴즈 미응시(quiz_avg_score NULL) 학생이 섞이면 전체 적합이
    # 실패하므로 여기서 떨궈내고, 몇 명이 빠졌는지는 표본수 판단에 필요하니 알린다.
    required = ["recency_days", "miss_streak_days", "quiz_avg_score", "duration", "event"]
    before = len(df)
    df = df.dropna(subset=required)
    if len(df) < before:
        print(f"[calibrate] 지표 결측으로 {before - len(df)}명 제외 (남은 표본 {len(df)}명)")
    return df.astype({c: float for c in required})


def load_synthetic_fallback(n=300):
    """scripts/coxph_synthetic_population.py의 생성 로직 재사용 - 실측 없을 때만 씀."""
    from scripts.coxph_synthetic_population import generate_population

    rng = np.random.default_rng(42)
    return generate_population(n, rng)


def fit_normalized_weights(df):
    for col in ["recency_days", "miss_streak_days", "quiz_avg_score"]:
        df[f"{col}_z"] = (df[col] - df[col].mean()) / df[col].std()

    cph = CoxPHFitter()
    cph.fit(
        df[["duration", "event", "recency_days_z", "miss_streak_days_z", "quiz_avg_score_z"]],
        duration_col="duration", event_col="event",
    )
    coef = cph.params_
    # quiz는 방향이 반대(높을수록 이탈 감소)라 부호 뒤집어서 "위험 기여도" 크기로 비교
    magnitudes = {
        "recency": abs(coef["recency_days_z"]),
        "streak": abs(coef["miss_streak_days_z"]),
        "quiz": abs(coef["quiz_avg_score_z"]),
    }
    total = sum(magnitudes.values())
    return {k: v / total for k, v in magnitudes.items()}, cph


def print_drift_report(fitted_weights, is_synthetic):
    print(f"\n{'축':<10} {'현재(domain/risk.py)':>20} {'실측/synthetic 적합':>20} {'드리프트':>10}")
    max_drift = 0.0
    for axis in ("recency", "streak", "quiz"):
        current = CURRENT_WEIGHTS[axis]
        fitted = fitted_weights[axis]
        drift = abs(fitted - current)
        max_drift = max(max_drift, drift)
        print(f"{axis:<10} {current:>20.3f} {fitted:>20.3f} {drift:>10.3f}")

    if is_synthetic:
        print("\n[SYNTHETIC FALLBACK] 실측 데이터가 아니라 synthetic 데이터로 적합한 결과임 -")
        print("실제 가중치 교체 근거로 쓰지 말 것. DB_HOST 등 환경변수 설정 후 재실행 필요.")
    elif max_drift >= DRIFT_WARNING_THRESHOLD:
        print(f"\n[교체 검토] 드리프트가 {DRIFT_WARNING_THRESHOLD} 이상 - domain/risk.py의 가중치를 이 실측값으로 갱신 검토.")
    else:
        print("\n[유지] 드리프트가 작음 - 현재 상수 유지해도 무방.")


def main():
    sys.stdout.reconfigure(encoding="utf-8")

    is_synthetic = False
    try:
        df = load_real_population()
        if len(df) < MIN_REAL_EVENTS_FOR_CALIBRATION or df["event"].sum() < MIN_REAL_EVENTS_FOR_CALIBRATION:
            print(f"실측 이벤트가 {MIN_REAL_EVENTS_FOR_CALIBRATION}건 미만 - synthetic으로 대체.")
            df = load_synthetic_fallback()
            is_synthetic = True
    except Exception as e:  # noqa: BLE001 - DB 미설정 등 어떤 이유로든 실측 조회 실패 시 폴백
        print(f"실측 데이터 조회 실패({e}) - synthetic으로 대체.")
        df = load_synthetic_fallback()
        is_synthetic = True

    fitted_weights, cph = fit_normalized_weights(df)
    print_drift_report(fitted_weights, is_synthetic)


if __name__ == "__main__":
    main()
