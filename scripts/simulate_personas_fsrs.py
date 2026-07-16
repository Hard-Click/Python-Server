"""페르소나별 FSRS 리뷰로그 시뮬레이션 (합성 데이터 생성).

Module05 데모 페르소나(김첫날/박모범/이눈치/최밀림/정위험)가 실제로 며칠~몇 달에
걸쳐 퀴즈를 보는 상황을 datetime을 직접 제어해 재현한다. domain/review.py,
domain/risk.py 만 사용하고 DB는 건드리지 않는다 (순수 로직 검증용).

리뷰 시점은 "TODAY로부터 며칠 전(days_ago)"으로 직접 정의한다 — 페르소나마다
가입 시점이 다른데 절대 캘린더 day 카운터를 공유하면 recency가 왜곡되기 때문
(예: 6개월 활동한 모범생인데 시뮬레이션이 2달치만 만들어놓고 나머지 4달을
"무응답"으로 잘못 해석하는 버그가 초안에서 실제로 발생했음).

실행: python -m scripts.simulate_personas_fsrs
"""
import random
import sys
from datetime import datetime, timedelta, timezone

from domain.review import review_lesson
from domain.risk import compute_rule_based_risk, risk_label

TODAY = datetime(2026, 7, 9, tzinfo=timezone.utc)  # 관찰 시점(risk 계산 기준)

# 수능일까지 남은 날짜로 가정한 값(데모용) — 실제로는 SubscriptionRepository.get_suneung_date로 조회.
DEMO_MAX_INTERVAL_DAYS = 120


def run_persona(name, days_ago_score_pairs, rng):
    """days_ago_score_pairs: [(TODAY로부터 며칠 전에 리뷰했는지, 퀴즈점수)] — 오래된 순 정렬."""
    card = None
    log = []
    prev_review_dt = None
    for days_ago, score in days_ago_score_pairs:
        review_dt = TODAY - timedelta(days=days_ago)
        card, due = review_lesson(card, score, review_datetime=review_dt, max_interval_days=DEMO_MAX_INTERVAL_DAYS)
        due_in_days = (due - review_dt).days
        log.append({
            "days_ago": days_ago,
            "score": score,
            "stability": round(card.stability, 2),
            "difficulty": round(card.difficulty, 2),
            "next_due_in_days": due_in_days,
        })
        prev_review_dt = review_dt

    last = log[-1]
    recency_days = last["days_ago"]  # TODAY 기준 마지막 리뷰 이후 경과일
    # miss_streak: 마지막 리뷰가 예정한 다음 복습일(due)을 며칠 넘겼는지
    miss_streak_days = max(0, recency_days - last["next_due_in_days"])
    quiz_avg_score = sum(score for _, score in days_ago_score_pairs) / len(days_ago_score_pairs)

    risk = compute_rule_based_risk(recency_days, miss_streak_days, quiz_avg_score)
    return {
        "name": name,
        "num_reviews": len(days_ago_score_pairs),
        "final_stability": last["stability"],
        "final_difficulty": last["difficulty"],
        "recency_days": recency_days,
        "miss_streak_days": miss_streak_days,
        "quiz_avg_score": round(quiz_avg_score, 1),
        "risk_score": risk,
        "risk_label": risk_label(risk),
        "log": log,
    }


def build_personas(rng):
    personas = {}

    # 김첫날: 가입 5일차, 리뷰 3번뿐(콜드스타트 시연). due일에 정확히 맞춰 리뷰, 방금까지 활동 중.
    personas["김첫날"] = [(5, 80), (4, 75), (2, 85)]

    # 박모범: 180일 전 가입, 꾸준히 매 due일에 맞춰 고득점(85~98), 바로 며칠 전까지도 활동.
    plan = []
    days_ago = 180
    while days_ago > 2:
        score = rng.randint(85, 98)
        plan.append((days_ago, score))
        days_ago -= rng.choice([4, 6, 8, 10])  # FSRS가 늘려주는 간격을 근사
    personas["박모범"] = plan

    # 이눈치: 90일 전 가입, 통과선(70) 바로 위로만 벼락치기, 항상 due를 넘겨 늦게 리뷰. 최근에도 간신히 활동.
    plan = []
    days_ago = 90
    while days_ago > 5:
        score = rng.choice([69, 71, 73, 91, 68, 72])
        plan.append((days_ago, score))
        days_ago -= rng.choice([6, 8, 10])
    personas["이눈치"] = plan

    # 최밀림: 60일 전 가입, 계속 늦게 리뷰하고 점수도 낮은 편(50~65). 지금도 밀려있지만 완전 이탈은 아님.
    plan = []
    days_ago = 60
    gap = 5
    while days_ago > 12:
        score = rng.randint(50, 65)
        plan.append((days_ago, score))
        days_ago -= gap
        gap += 1  # 리뷰할수록 간격(밀림)이 갈수록 커짐
    personas["최밀림"] = plan

    # 정위험: 100일 전 가입, 초반엔 정상이었으나 점수·주기 모두 악화되다 65일 전부터 완전 무응답(이탈).
    personas["정위험"] = [(100, 80), (85, 70), (72, 60), (65, 45)]

    return personas


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    rng = random.Random(42)
    personas = build_personas(rng)

    results = [run_persona(name, plan, rng) for name, plan in personas.items()]

    header = (
        f"{'페르소나':<8} {'리뷰수':>5} {'stability':>10} {'difficulty':>10} {'recency':>8} "
        f"{'streak':>7} {'quiz_avg':>9} {'risk':>6} {'label':>6}"
    )
    print(header)
    for r in results:
        print(
            f"{r['name']:<8} {r['num_reviews']:>5} {r['final_stability']:>10} {r['final_difficulty']:>10} "
            f"{r['recency_days']:>8} {r['miss_streak_days']:>7} {r['quiz_avg_score']:>9} "
            f"{r['risk_score']:>6} {r['risk_label']:>6}"
        )

    return results


if __name__ == "__main__":
    main()
