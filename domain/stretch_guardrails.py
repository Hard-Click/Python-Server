"""stretch_factor 가드레일 (순수 로직) - "좋은 sf를 고르는" 게 아니라 "sf가 사고를 못 치게 막는" 것.

설계 원칙:
  - **결과 기준(sf 값 기준 아님):** `sf <= 0.2` 같은 값 제한이 아니라 그 sf가 만드는 *결정 결과*
    (완주 불가·연장 증가·주간부하 급증)로 건다.
  - **진짜 저점을 올린다:** 어떤 sf든 최악 결과 자체를 막으므로 특정 값을 안 골라도(모집단을 몰라도)
    worst-case가 올라간다.
  - **infeasibility는 근사 아닌 실제 스케줄 결과로 판정:** 코스 간 주간시간 경쟁까지 반영해야 진짜
    완주 불가를 놓치지 않는다(그래서 application이 shadow CP-SAT를 실제로 풀어 feasibility를 넘긴다).
  - **hard vs soft:** hard(치명/abstain)는 무조건 baseline 폴백, soft(연장/부하)는 완화(dampen).
  - **abstain:** 신호가 아예 없으면(모든 코스 콜드스타트) 안 건드림.

sf 최적화가 아니라 사고 방지 장치다.
"""
from domain.scheduler import MIN_EFFICIENCY_SAMPLES

# 정책 선언(placeholder, PO 확정 대상).
WEEKLY_LOAD_SURGE_CAP_PCT = 30.0     # sf 유발 주간부하 증가율 상한
WEEKLY_LOAD_SURGE_ABS_MIN = 30.0     # 분모 하한 방어: 절대 증가가 이 분/주 미만이면 급증으로 안 봄


def evaluate_guardrails(applied_feasible, shadow_feasible, applied_ext, shadow_ext,
                        completed_counts, weekly_delta_pct, weekly_delta_abs):
    """제안된 variant를 baseline 대비 평가. feasibility/ext는 실제 CP-SAT 결과(근사 아님).

    completed_counts: 코스별 완료 건수 리스트(단일 스칼라 아님 - #1 지적 반영).
    반환: {triggered, fired, hard, soft, would_have_failed, action, numbers}.
    action=fallback_baseline(hard) | dampen(soft만) | allow.
    """
    hard, soft = [], []
    would_have_failed = False

    # abstain: '모든' 코스가 콜드스타트일 때만(=신호가 아예 없음). '하나라도 미만이면 폴백'은
    # 과보수적 - 콜드스타트 코스는 compute_efficiency_coefficient가 이미 coeff=1.0으로 self-abstain
    # 하므로, 성숙한 코스의 신호까지 버릴 이유가 없다. max<MIN이면 애초에 적용할 신호가 없다.
    if completed_counts and max(completed_counts) < MIN_EFFICIENCY_SAMPLES:
        hard.append("abstain_no_signal")

    # H1 치명(가장 강함, 실제 스케줄 기준): baseline은 완주 가능한데 variant는 불가 -> variant가 사고 유발.
    if applied_feasible and not shadow_feasible:
        hard.append("infeasibility")
        would_have_failed = True

    # 소프트: 사고(완주 불가)까진 아니지만 부담 증가.
    if shadow_ext > applied_ext:
        soft.append("extension_increase")
    # 주간부하 급증: 퍼센트 + 절대증가 둘 다 넘어야(작은 baseline에서 퍼센트 과장되는 것 방지 - #4).
    if weekly_delta_pct > WEEKLY_LOAD_SURGE_CAP_PCT and weekly_delta_abs > WEEKLY_LOAD_SURGE_ABS_MIN:
        soft.append("weekly_load_surge")

    action = "fallback_baseline" if hard else ("dampen" if soft else "allow")
    return {
        "triggered": bool(hard or soft),
        "fired": hard + soft,
        "hard": hard,
        "soft": soft,
        "would_have_failed": would_have_failed,
        "action": action,
        "numbers": {
            "applied_ext": applied_ext,
            "shadow_ext": shadow_ext,
            "applied_feasible": applied_feasible,
            "shadow_feasible": shadow_feasible,
            "weekly_delta_pct": round(weekly_delta_pct, 1),
            "weekly_delta_abs": round(weekly_delta_abs, 1),
        },
    }
