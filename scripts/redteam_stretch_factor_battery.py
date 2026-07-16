"""Phase 1 반례 배터리 - '좋은 sf 찾기'가 아니라 '위험한 sf 값을 양쪽에서 잘라내기'.

Phase 0(analyze_..._decision_sensitivity.py)가 "sf가 결정을 얼마나 바꾸나(구조)"를 봤다면,
여기는 "어떤 sf가 명백히 위험한가"를 관측 가능한 위험 축으로 판정해서 후보 구간을 좁힌다.
핵심 원칙 3가지:
  1. 잠재타입(진짜 느림/딴짓형) 안 씀 - 관측 가능한 현상(raw, 완료건수, 캡, D-day)으로만 위험 정의.
     (타입을 pass/fail에 넣으면 synthetic truth를 심는 순환논증이 됨.)
  2. 특정 값(0.5 등)을 원점/정답으로 두지 않음 - 위험은 sf 축 위 어디서든 나올 수 있고,
     저sf 위험(과소반응)과 고sf 위험(과부하)을 대칭으로 본다.
  3. 탈락 임계값은 '데이터에서 나온 진실'이 아니라 '정책 우선순위의 선언'이다 - 아래 THRESHOLD는
     전부 PO 확정 대상 placeholder. 이 스크립트가 하는 건 "임계값을 정하면 어디가 잘리는지"를
     보여주는 것까지.

계수·클램프·콜드스타트·단조성 같은 계수 레벨 불변식은 이미 tests/test_domain.py가 커버함 -
여기선 그 위, '운영 결정' 레벨의 위험만 본다.

실행: python -m scripts.redteam_stretch_factor_battery
"""
import math
import sys

import numpy as np

from domain.scheduler import compute_efficiency_coefficient, SLIP_BUFFER_WEEKS
from scripts.analyze_stretch_factor_decision_sensitivity import (
    BUCKETS, N_PER_BUCKET, SWEEP, build_population, decide, discrete_state,
)

# --- 소프트(정책) 탈락 임계값: 전부 PO 확정 대상 placeholder, 방향 중립. 하드 반례와 다르다. ---
UNDERREACT_MAX_PCT = 20.0      # 관측상 뚜렷이 느린데(raw>=1.3) 시스템이 사실상 무반응(coeff≈1.0)인 비율 상한
INDUCED_PUSH_MAX_PCT = 25.0    # sf 때문에 새로 push_mode로 들어간 학생 비율 상한(과압박)
INSTABILITY_MAX_PCT = 10.0     # raw ±0.05 흔들었을 때 이산결정이 뒤집히는 학생 비율 상한(벼랑)
CATASTROPHE_MAX_PCT = 0.5      # 하드 반례: 이 % 넘게 '치명(불가능)'이 나오면 그 sf는 무조건 탈락(정책 아님)
OBS_SLOW_RAW = 1.3             # '관측상 뚜렷이 느림'의 관측 기준(잠재타입 아님, 그냥 raw 임계)
NOREACT_COEFF_BAND = 0.05      # coeff가 1.0에서 이 이내면 '사실상 무반응'으로 본다
RAW_PERTURB = 0.05             # 결정 불안정성 검사용 raw 섭동폭


def _uncapped_extension_need(student, sf):
    """compute_required_extension_weeks가 하드캡(SLIP_BUFFER_WEEKS)으로 클램핑하기 '전'의 원생 필요치.
    이게 하드캡을 넘으면 = 연장·push 다 써도 물리적으로 못 끝냄 = 치명."""
    completed = [{"expected_duration_min": 100, "actual_duration_min": 100 * student["raw"]}] * student["completed"]
    coeff = compute_efficiency_coefficient(completed, stretch_factor=sf)
    needed = 0
    for c in student["courses"]:
        total = c["base_min"] * coeff
        available = student["cap"] * (c["deadline_week"] + 1)
        shortfall = total - available
        if shortfall > 0:
            needed = max(needed, math.ceil(shortfall / student["cap"]))
    return needed


def catastrophe_rate(pop, sf):
    """하드 반례: sf 때문에 '모든 안전장치(연장 하드캡)로도 완주 불가능'이 된 학생 비율.
    sf=0(강사 원안, 스트레치 없음)에선 가능했는데 이 sf로 당겨서 불가능해진 경우만(sf 유발분).
    이건 정책 임계값 문제가 아니라 '그 sf가 사고를 만든다'는 하드 실패 - 나오면 무조건 탈락."""
    induced = 0
    for s in pop:
        base_infeasible = _uncapped_extension_need(s, 0.0) > SLIP_BUFFER_WEEKS
        now_infeasible = _uncapped_extension_need(s, sf) > SLIP_BUFFER_WEEKS
        if now_infeasible and not base_infeasible:
            induced += 1
    return 100 * induced / len(pop)


def induced_push_rate(pop, sf):
    """소프트(과압박): sf 때문에 새로 push_mode로 들어간 학생 비율. push_mode 자체는 설계된
    정상 반응이라 '치명'은 아니지만, 스트레치를 세게 줘서 다수를 몰아붙이면 운영 리스크.
    기준 sf=0은 '개인화 안 함' 원리적 null(0.5 편향과 무관)."""
    induced = 0
    for s in pop:
        base_push = decide(s, 0.0)["push"] == "push_mode"
        now_push = decide(s, sf)["push"] == "push_mode"
        if now_push and not base_push:
            induced += 1
    return 100 * induced / len(pop)


def underreact_rate(pop, sf):
    """관측상 뚜렷이 느린(raw>=OBS_SLOW_RAW) 학생 중, 시스템이 사실상 무반응(coeff가 1.0 근처)인
    비율. 저sf에서 커짐 - 보이는 지체를 무시하고 강사 추정치대로 빡빡하게 짜서 예정된 슬립을 부름."""
    slow = [s for s in pop if s["raw"] >= OBS_SLOW_RAW]
    if not slow:
        return 0.0
    noreact = 0
    for s in slow:
        completed = [{"expected_duration_min": 100, "actual_duration_min": 100 * s["raw"]}] * s["completed"]
        coeff = compute_efficiency_coefficient(completed, stretch_factor=sf)
        if s["completed"] >= 3 and abs(coeff - 1.0) < NOREACT_COEFF_BAND:
            noreact += 1
    return 100 * noreact / len(slow)


def instability_rate(pop, sf):
    """raw를 ±RAW_PERTURB 흔들었을 때 이산결정(ext,push)이 뒤집히는 학생 비율. 특정 sf에서
    결정이 벼랑(cliff)처럼 튀면 그 값 대는 위험(작은 관측오차에 스케줄이 요동)."""
    flip = 0
    for s in pop:
        base = discrete_state(decide(s, sf))
        for delta in (+RAW_PERTURB, -RAW_PERTURB):
            pert = dict(s, raw=max(0.4, s["raw"] + delta))
            if discrete_state(decide(pert, sf)) != base:
                flip += 1
                break
    return 100 * flip / len(pop)


def _bands(sfs):
    if not sfs:
        return "없음"
    out, start, prev = [], sfs[0], sfs[0]
    for sf in sfs[1:]:
        if round(sf - prev, 2) <= 0.05:
            prev = sf
        else:
            out.append((start, prev)); start = prev = sf
    out.append((start, prev))
    return ", ".join(f"[{a:.2f}, {b:.2f}]" for a, b in out)


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    rng = np.random.default_rng(42)
    pop = build_population(rng)

    print(f"학생 {len(pop)}명 ({len(BUCKETS)}버킷×{N_PER_BUCKET}), 시드 42. 반례 배터리 = 위험 sf 제거.")
    print("두 종류를 구분한다: (A) 하드 반례=안전장치로도 불가능한 치명(정책 아님, 나오면 무조건 탈락),")
    print("(B) 소프트 트레이드오프=임계값을 어디 두냐에 달린 정책 판단(임계값은 전부 PO 확정 대상).\n")

    rows = []
    for sf in SWEEP:
        rows.append({
            "sf": sf,
            "cat": catastrophe_rate(pop, sf),
            "ur": underreact_rate(pop, sf),
            "push": induced_push_rate(pop, sf),
            "ins": instability_rate(pop, sf),
        })

    print(f"{'sf':>5} {'[A]치명%':>8} {'과소반응%':>9} {'유발push%':>9} {'불안정%':>8}  판정")
    hard_ok, soft_ok = [], []
    for r in rows:
        hard = r["cat"] > CATASTROPHE_MAX_PCT
        soft = []
        if r["ur"] > UNDERREACT_MAX_PCT:
            soft.append("과소반응")
        if r["push"] > INDUCED_PUSH_MAX_PCT:
            soft.append("유발push")
        if r["ins"] > INSTABILITY_MAX_PCT:
            soft.append("불안정")
        if hard:
            verdict = "탈락:치명"
        elif soft:
            verdict = "정책탈락(" + ",".join(soft) + ")"
        else:
            verdict = "생존"
        if not hard:
            hard_ok.append(r["sf"])
            if not soft:
                soft_ok.append(r["sf"])
        print(f"{r['sf']:>5.2f} {r['cat']:>7.1f}% {r['ur']:>8.1f}% {r['push']:>8.1f}% {r['ins']:>7.1f}%  {verdict}")

    print("\n=== 결과 ===")
    print(f"  [A] 하드 반례(치명) 통과 sf: {_bands(hard_ok)}")
    print("      → 여기서 잘린 값은 '정책 취향'이 아니라 '그 sf가 완주 불가능을 만든다'는 하드 실패.")
    print(f"  [B] 소프트 임계값(현재 placeholder)까지 통과 sf: {_bands(soft_ok)}")
    print("      → 이 경계는 PO가 임계값을 어디 두냐에 전적으로 달렸다. 임계값 바뀌면 경계도 바뀐다.")
    print("\n  주의 3가지:")
    print("  1) [B]는 아직 '안전구간'이 아니다 - worst-case/tail(Phase 2)까지 통과해야 그 이름을 쓴다.")
    print("  2) 이 스크립트는 살아남은 구간 안에서 어떤 값도 고르지 않는다(중립 규칙은 Phase 2 몫).")
    print("  3) 방향 확인용: 저sf는 과소반응, 고sf는 유발push/치명으로 잘리는 게 정상 - 특정 값 편애 아님.")


if __name__ == "__main__":
    main()
