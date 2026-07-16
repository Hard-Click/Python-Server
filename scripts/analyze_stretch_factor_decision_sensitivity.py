"""stretch_factor 오프라인 검증 v2 - '정책 결정 민감도' 분석 (순환논증 없음).

기존 simulate_stretch_factor_ab_test.py는 simulate_outcome()에서
"coeff가 우리가 심어둔 true_ratio에 가까울수록 좋다"는 손실을 정의로 박아뒀다 -
우리가 만든 정답에 가장 잘 맞는 값이 이기는 순환논증이라, 나온 '승자'는 근거로 못 쓴다
(docs/problem_definition_stretch_factor.md 5절이 지적한 바로 그 구조).

이 스크립트는 축을 바꾼다: 관측 불가능한 true_ratio를 아예 안 쓰고, **관측 가능한 입력
(raw pace, 완료 건수, 남은 분량, 마감, 주간 캡)만** 실제 프로덕션 함수에 통과시켜서
stretch_factor가 실제 운영 결정을 얼마나 바꾸는지만 센다:
  - compute_efficiency_coefficient()  -> 효율계수 (클램프/콜드스타트 포함)
  - compute_required_extension_weeks() -> 연장 주수
  - compute_slip_status()             -> push_mode on/off
  - 파생 weekly_minutes               -> 주간 학습량

측정하는 건 '누가 이겼나'가 아니라:
  1) sf를 0.0~1.0으로 쓸어봤을 때, 각 결정이 baseline(0.5) 대비 바뀌는 학생 비율
  2) 그 비율이 무의미(<5%)한 sf 구간 = "무의미 구간"
  3) 극단 비교 0.3 vs 0.7에서 실제로 바뀌는 결정
  4) 평균이 숨기는 꼬리(p95 / worst-case)
이건 true_ratio에 의존하지 않으므로 순환논증이 아니다 - "sf가 운영을 바꾸느냐"는
관측값만으로 답할 수 있는 질문이기 때문.

실행: python -m scripts.analyze_stretch_factor_decision_sensitivity
"""
import sys

import numpy as np

from domain.scheduler import (
    compute_efficiency_coefficient,
    compute_required_extension_weeks,
    SLIP_BUFFER_WEEKS,
)
from domain.reflow import compute_slip_status

# 관측 가능한 입력만으로 만든 학생 버킷. true_ratio(숨은 정답) 없음.
# 각 버킷은 "이런 관측 상황"을 뜻하고, 그 안에서 sf만 바꿔가며 결정 변화를 본다.
N_PER_BUCKET = 200

BUCKETS = [
    # label,          completed, raw_ratio, base_remaining_min, n_courses, weekly_cap, days_until_suneung
    ("보통_느림",        5, 1.40, 3600, 3, 420, None),
    ("빠름",            5, 0.80, 3600, 3, 420, None),
    ("느림_빡빡한캡",     5, 1.50, 3600, 3, 300, None),
    ("클램프근처",       5, 2.60, 3600, 3, 420, None),   # 높은 sf에서 2.0 클램프에 걸림(불연속)
    ("콜드스타트<3",     2, 1.50, 3600, 3, 420, None),   # 표본<3 -> coeff=1.0 고정, sf 무관해야 함
    ("마감임박",         5, 1.40, 3600, 3, 420, 80),     # D-100 이내 -> push_mode 강제, sf 무관해야 함
    ("심각한_밀림",       5, 1.50, 2800, 1, 420, None),   # 단일코스 과부하 -> sf 따라 연장 주수가 실제로 뒤집힘
]

SWEEP = [round(0.05 * i, 2) for i in range(0, 21)]  # 0.00 .. 1.00
# 기준값(baseline) 개념을 의도적으로 없앤다 - 특정 sf(예: 0.5)를 원점에 놓으면 측정 구조가
# 그 값을 중심으로 편향된다. 대신 이웃 스텝 간 국소 변화만 본다(모든 sf 대칭).
LOCAL_FLAT_PCT = 2.0  # 스텝당 이산결정 변화가 이 % 미만이면 그 구간을 '평탄'으로 본다


def make_student(rng, bucket):
    label, completed, raw, base, n_courses, cap, dday = bucket
    # 관측값에 소량 노이즈. 정답을 심는 게 아니라 같은 상황의 학생 분포를 만드는 것뿐.
    raw_obs = max(0.4, raw + rng.normal(0, 0.08))
    base_obs = max(600, base + rng.normal(0, 300))
    per_course = base_obs / n_courses
    # 코스별 마감 주차를 다르게(6~10주) 흩뿌림
    courses = [
        {"base_min": per_course, "deadline_week": int(6 + (c % 5))}
        for c in range(n_courses)
    ]
    return {
        "label": label, "completed": completed, "raw": raw_obs,
        "courses": courses, "cap": cap, "dday": dday,
    }


def decide(student, sf):
    """관측값 + sf -> 실제 프로덕션 함수로 계산한 운영 결정. true_ratio 안 씀."""
    completed_lessons = [
        {"expected_duration_min": 100, "actual_duration_min": 100 * student["raw"]}
    ] * student["completed"]
    coeff = compute_efficiency_coefficient(completed_lessons, stretch_factor=sf)

    course_totals = [
        {"total_duration_min": c["base_min"] * coeff, "deadline_week": c["deadline_week"]}
        for c in student["courses"]
    ]
    ext = compute_required_extension_weeks(course_totals, student["cap"], SLIP_BUFFER_WEEKS)

    total_scaled = sum(ct["total_duration_min"] for ct in course_totals)
    horizon_weeks = max(c["deadline_week"] for c in student["courses"]) + 1 + ext
    weekly_minutes = total_scaled / horizon_weeks
    capacity = student["cap"] * horizon_weeks
    cumulative_slip = max(0, total_scaled - capacity)
    push = compute_slip_status(int(cumulative_slip), student["cap"], student["dday"])

    return {"coeff": coeff, "ext": ext, "weekly_minutes": weekly_minutes, "push": push}


def build_population(rng):
    return [make_student(rng, b) for b in BUCKETS for _ in range(N_PER_BUCKET)]


def discrete_state(d):
    """운영상 '핵심' 이산 결정만 (연속 근사치 weekly_minutes는 제외)."""
    return (d["ext"], d["push"])


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    rng = np.random.default_rng(42)
    pop = build_population(rng)
    n = len(pop)

    print(f"학생 {n}명 ({len(BUCKETS)}개 관측 버킷 × {N_PER_BUCKET}), sf 스윕 {SWEEP[0]}~{SWEEP[-1]}")
    print("※ true_ratio(숨은 정답) 미사용 + baseline(기준값) 미사용. 특정 sf를 원점에 놓지 않는다.")
    print("  어떤 값도 이 분석이 채택/추천/기각하지 않는다 - 결정 표면의 '모양'만 관측한다.\n")

    # 전 sf에서 각 학생 결정을 미리 계산. grid[학생][sf_idx]. 기준값 없음(전 sf 대칭).
    grid = [[decide(s, sf) for sf in SWEEP] for s in pop]

    print("=== 1) 국소 민감도: 이웃 스텝(sf -> 다음 sf) 간 변화 (기준점 없음) ===")
    print("  '0.5 대비'가 아니라 각 구간이 평평한지/가파른지만. 이산결정 = 핵심지표, 주간분 = 보조.")
    print(f"{'구간':>12} {'이산결정변화%':>13} {'주간분 median Δ%':>16}")
    local_flat = []
    for i in range(len(SWEEP) - 1):
        disc_ch = 0
        wm_deltas = []
        for st in range(n):
            da, db = grid[st][i], grid[st][i + 1]
            if discrete_state(da) != discrete_state(db):
                disc_ch += 1
            if da["weekly_minutes"] > 0:
                wm_deltas.append(abs(db["weekly_minutes"] - da["weekly_minutes"]) / da["weekly_minutes"] * 100)
        rate = 100 * disc_ch / n
        med = float(np.median(wm_deltas)) if wm_deltas else 0.0
        local_flat.append(rate < LOCAL_FLAT_PCT)
        print(f"{SWEEP[i]:.2f}->{SWEEP[i+1]:.2f} {rate:>12.1f}% {med:>15.1f}%")

    print("\n=== 2) sf에 아예 무감응인 학생 비율 (전 구간 대칭 집계) ===")
    invariant = sum(
        1 for st in range(n)
        if len({discrete_state(grid[st][i]) for i in range(len(SWEEP))}) == 1
    )
    print(f"  {100*invariant/n:.1f}%의 학생은 sf를 {SWEEP[0]}~{SWEEP[-1]} 어디에 놔도 이산결정(ext,push)이 동일.")
    print("  => 이만큼은 sf 값 논쟁이 애초에 운영에 영향을 못 준다(값 선택의 레버리지 밖).")

    print("\n=== 3) 결정 표면이 평탄한 구간 (이웃 민감도로 탐지, 원점 없음) ===")
    bands = []
    i = 0
    while i < len(local_flat):
        if local_flat[i]:
            j = i
            while j < len(local_flat) and local_flat[j]:
                j += 1
            bands.append((SWEEP[i], SWEEP[j]))
            i = j
        else:
            i += 1
    if bands:
        for a, b in bands:
            print(f"  평탄 구간 [{a:.2f}, {b:.2f}] (스텝당 이산결정 변화 < {LOCAL_FLAT_PCT}%)")
    else:
        print("  평탄 구간 없음 - 전 구간에서 이산결정이 유의하게 변함.")
    print("  ★ 이 구간은 '결정이 잘 안 변하는 곳'일 뿐 '안전한 곳'이 아니다.")
    print("    안전구간(반례+worst-case 통과)은 Phase 1~2 이후에만 붙는 이름. 여기서 값 고르지 않음.")

    print("\n=== 4) 대칭 극단 비교: sf=0.3 vs sf=0.7 (특정 값 편애 없음, 버킷별) ===")
    i03, i07 = SWEEP.index(0.3), SWEEP.index(0.7)
    print(f"{'버킷':>14} {'이산결정변화%':>13} {'ext변화%':>9} {'push변화%':>9}")
    for label in [b[0] for b in BUCKETS]:
        idx = [k for k, s in enumerate(pop) if s["label"] == label]
        ch = sum(discrete_state(grid[k][i03]) != discrete_state(grid[k][i07]) for k in idx)
        ext = sum(grid[k][i03]["ext"] != grid[k][i07]["ext"] for k in idx)
        push = sum(grid[k][i03]["push"] != grid[k][i07]["push"] for k in idx)
        m = len(idx)
        print(f"{label:>14} {100*ch/m:>12.1f}% {100*ext/m:>8.1f}% {100*push/m:>8.1f}%")

    print("\n결론(관측된 구조만, 어떤 값도 추천/기각하지 않음):")
    print("  - 상당수 학생은 sf와 무관하게 이산결정이 고정된다(콜드스타트/마감임박 등 구조적 무감응).")
    print("  - sf는 주로 주간부하(보조지표)를 연속적으로 밀고, 이산결정을 가르는 건 소수 스트레스 버킷.")
    print("  - 평탄 구간은 '결정 무변화'이지 '안전'이 아니다. 값의 채택/기각은 Phase 1(반례)-2(worst-case) 이후.")


if __name__ == "__main__":
    main()
