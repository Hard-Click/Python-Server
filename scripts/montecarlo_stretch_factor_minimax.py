"""Phase 2 - 몬테카를로 + minimax. '평균이 좋은 값'이 아니라 '최악에서 덜 망가지는 구간'.

Phase 1(redteam_battery)의 한계: 위험 구간이 고정 합성 버킷 비중에 민감했다. Phase 2는 그
비중(모집단 구성) 자체를 **우리가 모르는 불확실성**으로 놓고, 수백 개 시나리오(느린 학생 비율,
캡 빡빡함, 밀린 비율, 마감 압박, 부하 스케일)를 랜덤 샘플링한다. 그 다음 각 sf에 대해 위험
지표의 '평균'이 아니라 **worst-case / p95(꼬리)**를 본다 - 모집단을 모를 때는 평균 최적화보다
minimax가 방어적이라서.

정직성 원칙(앞 Phase들과 동일):
  - true_ratio(숨은 정답) 안 씀 - 위험 지표는 전부 관측 가능한 것(치명·과소반응·유발push).
    따라서 순환논증 아님.
  - 특정 값(0.5)을 원점/정답으로 두지 않음. sf=0 기준은 '개인화 안 함' 원리적 null일 뿐.
  - **손실 가중치(W_*)는 데이터에서 나온 진실이 아니라 '정책 우선순위의 선언'이다.** 그래서
    기본 가중치의 minimax 결과와 함께, 가중치를 바꾸면 답이 어떻게 흔들리는지(민감도)도 같이 낸다.
  - 결론은 점추정이 아니라 **구간**. 구간 안에서 값 선택은 이 스크립트가 하지 않는다.

실행: python -m scripts.montecarlo_stretch_factor_minimax
"""
import sys

import numpy as np

from scripts.analyze_stretch_factor_decision_sensitivity import SWEEP
from scripts.redteam_stretch_factor_battery import (
    catastrophe_rate, underreact_rate, induced_push_rate,
)

N_SCENARIOS = 150
N_STUDENTS = 140

# 손실 가중치 = 정책 선언(진실 아님). 치명은 하드 실패라 크게, 소프트는 작게. 아래 SENSITIVITY에서
# 이 선언을 흔들어 답이 얼마나 바뀌는지 본다.
DEFAULT_WEIGHTS = {"cat": 10.0, "under": 1.0, "push": 1.0}
SENSITIVITY_WEIGHTS = {
    "치명 중시(기본)": {"cat": 10.0, "under": 1.0, "push": 1.0},
    "과소반응 중시": {"cat": 3.0, "under": 3.0, "push": 0.5},
    "균형": {"cat": 5.0, "under": 2.0, "push": 2.0},
}


def sample_scenario(rng):
    """우리가 모르는 모집단 구성 knob들을 랜덤 추출. 각 knob이 '실세계가 이럴 수도 있다'의 한 점."""
    return {
        "p_obs_slow": rng.uniform(0.2, 0.8),      # 관측상 느린 학생 비율
        "slow_raw_mean": rng.uniform(1.2, 1.7),   # 느린 학생이 얼마나 느린가
        "cap": rng.uniform(300, 500),             # 주간 가용 분
        "load_scale": rng.uniform(0.7, 1.4),      # 남은 분량 스케일
        "p_behind": rng.uniform(0.1, 0.5),        # 단일코스 과부하(밀린) 학생 비율
        "p_deadline": rng.uniform(0.0, 0.3),      # D-100 이내 비율
        "p_coldstart": rng.uniform(0.05, 0.25),   # 완료<3 콜드스타트 비율
    }


def sample_population(rng, sc):
    pop = []
    for _ in range(N_STUDENTS):
        raw = (max(0.7, rng.normal(sc["slow_raw_mean"], 0.15))
               if rng.random() < sc["p_obs_slow"]
               else max(0.5, rng.normal(0.95, 0.1)))
        completed = rng.integers(0, 3) if rng.random() < sc["p_coldstart"] else 5
        dday = int(rng.uniform(30, 95)) if rng.random() < sc["p_deadline"] else None
        if rng.random() < sc["p_behind"]:
            base = max(600, rng.normal(2800, 300)) * sc["load_scale"]
            courses = [{"base_min": base, "deadline_week": 6}]  # 단일코스 과부하
        else:
            base = max(600, rng.normal(3600, 400)) * sc["load_scale"]
            courses = [{"base_min": base / 3, "deadline_week": int(6 + c)} for c in range(3)]
        pop.append({"label": "mc", "completed": int(completed), "raw": raw,
                    "courses": courses, "cap": sc["cap"], "dday": dday})
    return pop


def combined_loss(cat, under, push, w):
    return w["cat"] * cat + w["under"] * under + w["push"] * push


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    rng = np.random.default_rng(42)

    # 시나리오 × sf 그리드로 위험 지표 3종을 채운다.
    scenarios = [sample_population(rng, sample_scenario(rng)) for _ in range(N_SCENARIOS)]
    # risk[sf_idx] = {"cat":[...시나리오별...], "under":[...], "push":[...]}
    risk = {i: {"cat": [], "under": [], "push": []} for i in range(len(SWEEP))}
    for pop in scenarios:
        for i, sf in enumerate(SWEEP):
            risk[i]["cat"].append(catastrophe_rate(pop, sf))
            risk[i]["under"].append(underreact_rate(pop, sf))
            risk[i]["push"].append(induced_push_rate(pop, sf))

    print(f"시나리오 {N_SCENARIOS}개 × 학생 {N_STUDENTS}명, 시드 42. 각 sf의 위험을 평균 아닌 꼬리로 평가.")
    print("※ 손실 가중치는 정책 선언(진실 아님). 아래 민감도에서 선언을 흔들어 답 변화를 본다.\n")

    print("=== 1) sf별 위험 지표: worst-case(시나리오 최댓값) / p95 ===")
    print(f"{'sf':>5} {'치명 wc%':>9} {'치명 p95%':>9} {'과소반응 wc%':>12} {'유발push wc%':>12}")
    for i, sf in enumerate(SWEEP):
        cat_wc = max(risk[i]["cat"]); cat_p95 = float(np.percentile(risk[i]["cat"], 95))
        un_wc = max(risk[i]["under"]); pu_wc = max(risk[i]["push"])
        print(f"{sf:>5.2f} {cat_wc:>8.1f}% {cat_p95:>8.1f}% {un_wc:>11.1f}% {pu_wc:>11.1f}%")

    print("\n=== 2) minimax: worst-case 결합손실이 최소인 sf (기본 가중치) ===")
    print(f"{'sf':>5} {'worst-case 결합손실':>18} {'평균 결합손실':>14}")
    wc_by_sf = {}
    for i, sf in enumerate(SWEEP):
        losses = [combined_loss(risk[i]["cat"][k], risk[i]["under"][k], risk[i]["push"][k], DEFAULT_WEIGHTS)
                  for k in range(N_SCENARIOS)]
        wc_by_sf[sf] = max(losses)
        print(f"{sf:>5.2f} {max(losses):>17.1f} {float(np.mean(losses)):>13.1f}")
    best_sf = min(wc_by_sf, key=wc_by_sf.get)
    best = wc_by_sf[best_sf]
    # 안전구간: worst-case 손실이 minimax 최적의 1.25배 이내인 sf들(점 대신 구간)
    tol = best * 1.25
    safe = [sf for sf in SWEEP if wc_by_sf[sf] <= tol]
    print(f"\n  minimax 최적(단일점): sf={best_sf:.2f} (worst-case 손실 {best:.1f})")
    print(f"  안전구간(최적의 1.25배 이내): [{min(safe):.2f}, {max(safe):.2f}]")

    print("\n=== 3) 민감도: 정책 가중치를 흔들면 minimax 답이 어떻게 움직이나 ===")
    print("  (가중치는 진실이 아니라 선언이므로, 이게 흔들리면 '점'이 아니라 '구간'으로 말해야 한다)")
    for name, w in SENSITIVITY_WEIGHTS.items():
        wc = {}
        for i, sf in enumerate(SWEEP):
            wc[sf] = max(combined_loss(risk[i]["cat"][k], risk[i]["under"][k], risk[i]["push"][k], w)
                         for k in range(N_SCENARIOS))
        b = min(wc, key=wc.get)
        band = [sf for sf in SWEEP if wc[sf] <= wc[b] * 1.25]
        print(f"  {name:>14}: minimax sf={b:.2f}, 안전구간 [{min(band):.2f}, {max(band):.2f}]")

    print("\n★ 근본 한계(반드시 같이 읽을 것): 이 minimax도 ground-truth 문제를 못 벗어난다.")
    print("  - '치명'(종이 위 완주불가)은 '관측된 느림이 진짜다 → 시간 더 배정 → 넘침'을 암묵 가정해")
    print("    고sf를 벌주고, '과소반응'은 반대로 저sf를 벌준다. 둘은 동시에 못 줄인다 -")
    print("    그 밑의 질문('관측된 느림이 진짜냐')이 바로 처음부터 관측 불가능했던 그 잠재변수라서.")
    print("  - 그래서 minimax 최적점(≈저sf)은 데이터가 아니라 '치명 축을 무겁게 둔 정책 선언의 그림자'다.")
    print("    이 스크립트는 어떤 값도 추천하지 않는다(0.5도, 0.2도).")
    print("\n결론(정직하게 주장 가능한 것만):")
    print("  - 방향성: 극단 저sf는 관측 지체 무시로, 극단 고sf는 밀린 학생 완주불가로 지배열등 - 양끝은 나쁘다.")
    print("  - 점 선택 불가: '안전한 점 sf'는 정책 가중치에 따라 움직이므로 데이터로 정당화 못 한다.")
    print("  - 값의 확정은 오프라인으론 불가 - 잠재변수를 가르려면 실사용자가 필요. 그게 Phase 3의 이유.")
    print("  - 지금 코드의 placeholder는 '검증된 선택'이 아니라 '실측 전까지의 미검증 임시치'로만 취급.")


if __name__ == "__main__":
    main()
