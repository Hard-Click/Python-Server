"""[DEPRECATED - 순환논증이라 근거로 쓰지 말 것]

이 스크립트의 simulate_outcome()은 "coeff가 우리가 심어둔 true_ratio에 가까울수록 좋다"는
손실을 정의로 박아둔 순환논증이다(우리가 만든 정답에 가장 잘 맞는 값이 이기게 돼 있음 -
docs/problem_definition_stretch_factor.md 5절이 지적한 바로 그 구조). 따라서 여기서 나오는
'승자/outcome 평균'은 검증 근거로 인용 불가.

대체재: scripts/analyze_stretch_factor_decision_sensitivity.py
  - true_ratio(숨은 정답)를 안 쓰고, 관측 가능한 입력만 실제 프로덕션 함수에 통과시켜
    sf가 실제 운영 결정(extension_weeks/push_mode/weekly_minutes)을 바꾸는 비율만 측정한다.
이 파일은 "왜 순환논증 접근을 버렸는지"의 사료로만 남겨둔다(발표 시 before/after 대비용).

---
(이하 원본) 실사용자 없이 EFFICIENCY_STRETCH_FACTOR A/B 테스트를 가상 데이터로 시행착오해보는 스크립트.

핵심 문제의식(발표 포인트): "관찰되는 raw 페이스"만으로는 두 종류의 학생을 구분할 수
없다 - ①진짜로 이해가 느린 학생(느린 게 진짜 실력), ②집중을 안 해서 오래 걸리는 학생
(딴짓형, 사실은 밀어붙이면 더 잘할 수 있음). 이 둘은 raw efficiency 관찰값이 비슷하게
나올 수 있지만, 스트레치 팩터를 세게 줬을 때(1.0 쪽으로 강하게 당김) 정반대로 반응한다:
- 진짜 느린 학생: 스트레치를 세게 주면(실제 능력보다 빠른 페이스를 요구) 완주가 무너짐 -> 나쁨
- 딴짓형 학생: 스트레치를 세게 줘야(관찰된 게으른 페이스 대신 진짜 능력 쪽으로 당겨야) 좋아짐

이 긴장관계 때문에 "최적 스트레치 팩터"는 존재하지 않고, **모집단에서 두 유형의 비율에
따라 답이 달라진다** - 그 비율은 지금 우리가 모른다(실사용자가 없으니까). 그래서 이
스크립트는 "하나의 정답"을 내는 게 아니라, 비율 가정을 바꿔가며 답이 어떻게 흔들리는지
보여주는 민감도 분석(시행착오) 도구다. domain/experiments.py::assign_variant()와
domain/scheduler.py::compute_efficiency_coefficient()는 실제 프로덕션 코드를 그대로
가져다 쓴다(재구현 아님) - 그래야 시뮬레이션 결과가 실제 배정 로직과 어긋나지 않는다.

실행: python -m scripts.simulate_stretch_factor_ab_test
"""
import sys

import numpy as np

from domain.experiments import assign_variant
from domain.scheduler import compute_efficiency_coefficient

EXPERIMENT_NAME = "efficiency_stretch_factor"
VARIANTS = [0.3, 0.5, 0.7]

N_STUDENTS = 600

# 진짜 능력치(참값) 분포 - 관찰되지 않는 값. 시뮬레이션이라서 우리가 "정답"을 쥐고 있음.
GENUINE_SLOW_TRUE_RATIO_MEAN = 1.4   # 진짜 느린 학생: 관찰치와 진짜 능력치가 거의 같음
DISTRACTED_TRUE_RATIO_MEAN = 1.0     # 딴짓형: 집중하면 사실 평균 속도(1.0)
DISTRACTION_INFLATION_RANGE = (0.2, 0.6)  # 딴짓형의 raw 관찰치가 진짜 능력치보다 부풀려지는 정도

PENALTY_WEIGHT = 40  # 임의 스케일(시뮬레이션 내부 단위) - 결과의 "부호/순위"만 신뢰할 것


def generate_students(rng, p_distracted):
    """p_distracted: 모집단 중 딴짓형 비율(우리가 실제로는 모르는 값 - 시나리오별로 바꿔봄)."""
    students = []
    for i in range(N_STUDENTS):
        member_id = f"sim_student_{i}"
        is_distracted = rng.random() < p_distracted

        if is_distracted:
            true_ratio = max(0.7, rng.normal(DISTRACTED_TRUE_RATIO_MEAN, 0.1))
            inflation = rng.uniform(*DISTRACTION_INFLATION_RANGE)
            raw_ratio = true_ratio + inflation
        else:
            true_ratio = max(1.0, rng.normal(GENUINE_SLOW_TRUE_RATIO_MEAN, 0.1))
            raw_ratio = true_ratio + rng.normal(0, 0.05)  # 노이즈만, 부풀림 없음

        students.append({
            "member_id": member_id,
            "is_distracted": is_distracted,
            "true_ratio": true_ratio,
            "raw_ratio": raw_ratio,
        })
    return students


def simulate_outcome(effective_coefficient, true_ratio, is_distracted, rng):
    """실제 프로덕션에 없는, 이 시뮬레이션만의 가정 - '아웃컴 점수'는 완전히 임의 스케일.
    부호와 그룹간 상대적 순위만 의미 있다(절대값을 발표에서 실제 수치처럼 인용하면 안 됨)."""
    if is_distracted:
        # 너무 관대하게(효과계수가 진짜 능력보다 큼 = 게으른 페이스를 그대로 인정) 하면 손해
        penalty = max(0.0, effective_coefficient - true_ratio)
    else:
        # 너무 세게 밀면(효과계수가 진짜 능력보다 작음 = 무리한 페이스 강요) 손해
        penalty = max(0.0, true_ratio - effective_coefficient)

    return 100 - PENALTY_WEIGHT * penalty + rng.normal(0, 5)


def run_scenario(p_distracted, rng):
    students = generate_students(rng, p_distracted)
    outcomes_by_variant = {v: [] for v in VARIANTS}

    for s in students:
        variant = assign_variant(s["member_id"], EXPERIMENT_NAME, VARIANTS)
        completed = [{"expected_duration_min": 100, "actual_duration_min": 100 * s["raw_ratio"]}] * 5
        effective_coefficient = compute_efficiency_coefficient(completed, stretch_factor=variant)
        outcome = simulate_outcome(effective_coefficient, s["true_ratio"], s["is_distracted"], rng)
        outcomes_by_variant[variant].append(outcome)

    return {v: float(np.mean(outs)) for v, outs in outcomes_by_variant.items()}


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    rng = np.random.default_rng(42)

    print(f"학생 수: {N_STUDENTS}, variant: {VARIANTS}")
    print("※ 이건 'variant 중 승자를 찾는' 최적화 결과가 아니라 민감도(sensitivity) 리포트다 -")
    print("  아래 숫자·시나리오 가정은 전부 이 스크립트 안에서 우리가 만든 것이라, 절대값이나")
    print("  '어느 게 이겼다'를 실측 근거처럼 인용하면 안 된다. 확인하려는 건 딱 하나:")
    print("  '모집단 구성을 모르는 채로 하나의 상수를 고정해야 한다면, 그 상수가 시나리오")
    print("   전반에서 얼마나 안 망가지는가'다.\n")
    print(f"{'딴짓형 비율':>10}  " + "  ".join(f"stretch={v:.1f}" for v in VARIANTS) + "   시나리오 내 상대 우위")

    for p_distracted in [0.2, 0.35, 0.5, 0.65, 0.8]:
        results = run_scenario(p_distracted, rng)
        relative_best = max(results, key=results.get)
        row = "  ".join(f"{results[v]:>12.1f}" for v in VARIANTS)
        print(f"{p_distracted:>10.2f}  {row}   {relative_best}")

    print("\n결론(이 정도까지만 주장 가능): 모집단 구성비를 모르는 지금 시점엔, 극단값(0.3/0.7)은")
    print("특정 시나리오에서 크게 손해 볼 수 있는 반면, 중간값(0.5)은 어느 시나리오에서도 크게")
    print("밀리지 않는다 - '최적'이 아니라 '불확실성 하의 안전한 타협점'이라는 근거로만 쓸 것.")


if __name__ == "__main__":
    main()
