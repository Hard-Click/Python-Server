# stretch_factor 오프라인 검증 회고 (목표·방법·트레이드오프·시행착오·의미)

각 단계마다 **①목표 ②쓴 방법 ③제거한 대안과 그 트레이드오프 ④도달하려다 생긴 시행착오
⑤결과와 그에 상응하는 의미**로 정리한다. 실행 산출물은 [roadmap](roadmap_stretch_factor_offline.md),
철학은 [problem_definition](problem_definition_stretch_factor.md) 참조.

## 관통 원칙 (모든 단계 공통)
1. **ground truth(true_ratio) 안 씀** → 우리가 정답을 심고 그 정답으로 채점하는 순환논증 회피.
2. **특정 값(0.5)을 원점/정답으로 두지 않음** → 측정 구조가 특정 값으로 기우는 편향 회피.
3. **손실·임계값·가중치는 "정책 선언"임을 명시** → 데이터에서 나온 진실인 척 안 함.
4. **결론은 점이 아니라 방향/구간** → 없는 정밀도를 지어내지 않음.

---

## Phase 0 — 결정 민감도 분석

- **목표:** "sf가 실제 운영 결정(연장 주수·push_mode)을 얼마나 바꾸는가"의 *구조*를 순환논증 없이 파악.
- **방법:** 관측 가능한 입력만 실제 프로덕션 함수(`compute_efficiency_coefficient`/
  `compute_required_extension_weeks`/`compute_slip_status`)에 통과시켜, sf 변화에 따른 결정
  변화율만 측정. (`scripts/analyze_stretch_factor_decision_sensitivity.py`)
- **제거한 대안 ↔ 트레이드오프:**
  - *outcome-scoring 시뮬레이션*(기존 `simulate_stretch_factor_ab_test.py`): "variant 승자를
    뽑을 수 있다"는 매력 ↔ true_ratio를 손실 정의에 심어둬서 **결론이 가정을 되비출 뿐**(증거력 0).
    → 순환논증이라 폐기(deprecated 표시).
  - *A/B·IPS/SNIPS·설문*: 통계적으로 옳음 ↔ **로그·트래픽·응답자 0** → 적용 조건 자체가 불성립.
- **시행착오:** 처음엔 "baseline 0.5 대비 변화율"로 쟀다. 무의미 구간이 `[0.45, 0.55]`로 나왔는데,
  **이건 0.5를 원점에 둔 인공물**이었다(당연히 0.5 근처는 0.5 대비 변화가 최소). baseline을
  제거하고 이웃-스텝 기반 reference-free로 재작성하자 평탄 구간이 **고sf 쪽으로 이동**했다.
- **결과 → 의미:** (본 synthetic pop) 56.4%가 sf에 완전 무감, 콜드스타트/마감임박은 구조적 0%,
  sf는 이산 스위치보다 "주간 부하 다이얼". → **sf의 실제 레버리지가 생각보다 좁다.** 그리고 이
  시행착오 자체가 **"방법론이 특정 값으로 조용히 기울 수 있다"를 실증**한 첫 사건.

---

## Phase 1 — 반례 배터리

- **목표:** 좋은 값 찾기가 아니라 **위험한 sf를 양쪽에서 제거**(위험 구간 컷).
- **방법:** 위험을 둘로 분리 — **하드 반례(치명=연장 하드캡·push까지 다 써도 완주 불가능)** vs
  **소프트 트레이드오프(임계값이 정하는 정책 판단)**. 모든 위험을 관측 현상으로만 정의.
  (`scripts/redteam_stretch_factor_battery.py` + `tests/test_stretch_factor_redteam.py`)
- **제거한 대안 ↔ 트레이드오프:**
  - *잠재타입(진짜 느림/딴짓형) 기반 pass/fail*: 직관적 ↔ 타입을 판정 기준에 넣으면 **synthetic
    truth를 다시 심는 순환논증**. → 관측 현상(raw, completed, cap, D-day)으로만 정의로 대체.
  - *계수 레벨 속성 테스트*(클램프/단조성 등): 유용 ↔ **이미 `test_domain.py`가 커버** → 중복
    회피하고 "결정 레벨"에만 집중.
- **시행착오:** 과부하 지표를 처음엔 "주간부하 > 캡"으로 뒀더니 sf=0에서도 32%가 걸렸다 —
  **모집단이 원래 무거운 것**이지 sf 탓이 아니었다. → sf=0(개인화 안 함=원리적 null) 기준
  "유발분"으로 바꿨는데, 그것도 push_mode의 정상 영역까지 위험으로 잡았다. → 최종적으로
  **"연장 하드캡으로도 불가능"인 치명**으로 재정의. 또 extension 차원이 내내 0%(죽은 축)라
  단일코스 과부하 버킷(심각한_밀림)을 추가해 살렸다.
- **결과 → 의미:** (조건부) 하드 반례 통과 `[0.00, 0.25]`, **0.5는 치명 3.6%로 탈락.** →
  **방향 중립으로 짠 배터리가 현재값 0.5를 실제로 탈락시켰다 = 편향 제거가 말이 아니라 작동함.**
  단 구간 경계는 pop·임계값에 강하게 의존 → "0.5 위험 확정"이 아니라 **"0.5가 자동으로 안전하진
  않다는 반례"**로만 읽는다.

---

## Phase 2 — 몬테카를로 + minimax

- **목표:** 모집단 구성을 모를 때 **평균이 아니라 최악에서 덜 망가지는가**를 보고 방어적 구간 도출.
- **방법:** 모집단 구성(느린 학생 비율·캡·밀린 비율·마감 압박 등)을 **불확실성으로 두고** 수백
  시나리오 샘플링, 각 sf를 worst-case/p95로 평가, minimax. 손실 가중치는 정책 선언으로 명시하고
  민감도까지 냄. (`scripts/montecarlo_stretch_factor_minimax.py`)
- **제거한 대안 ↔ 트레이드오프:**
  - *평균 최적화*: 계산 단순 ↔ 모집단 모를 때 **꼬리 리스크를 숨김** → worst-case/minimax로 대체.
  - *Bayesian credibility / MAB*: 원칙적 ↔ 오픈 전 해커톤 리소스에 **오버스펙** → 스코프아웃.
- **시행착오(가장 중요):** minimax가 `sf≈0.15~0.20`을 "최적"으로 뱉었다. 이걸 새 답으로 밀 뻔
  했는데 — 그러면 **0.5 편향을 0.2 편향으로 바꾸는 것**이었다. 멈추고 보니, `치명` 지표는
  "관측된 느림이 진짜다"를 암묵 가정해 고sf를 벌주고 `과소반응`은 반대로 저sf를 벌준다. 둘은
  **동시에 최소화 불가능**한데, 그 밑의 질문("느림이 진짜냐")이 바로 관측 불가 잠재변수였다.
- **결과 → 의미:** **"안전한 점 sf는 오프라인으로 정당화 불가"를 증명.** minimax 최적점조차 정책
  가중치가 정하지 데이터가 정하지 않는다. → **몬테카를로도 ground-truth 문제를 못 벗어난다** —
  이게 오프라인의 한계를 확정한 핵심 산출이고, "값 선택"이라는 목표 자체를 정직하게 접게 만들었다.

---

## 운영 결정 — Shadow mode (숫자 대신 배포 정책)

- **목표:** "점을 못 고른다"가 결론이면, 코드에 필요한 값은 어떻게 두나 → **실측 전 저위험 실측
  수집 경로** 마련(운영 결정을 비워두지 않기).
- **방법:** 적용은 `sf=0.0`(강사 추정치, 기능 off, 유발 infeasibility 0), 배정 variant는 계산만
  해서 결정 델타를 real traffic 로그로 남김. 집계·dry-run·엔드포인트까지 구현.
  (`use_cases.py::SHADOW_MODE`, `domain/shadow_report.py`, `scripts/{report,dryrun}_shadow_*.py`,
  `GET /admin/shadow-summary`)
- **제거한 대안 ↔ 트레이드오프:**
  - *특정 sf 실제 적용*: 개인화 효과 기대 ↔ Phase 2가 정당화 불가라 결론 → 기각.
  - *즉시 opt-in 베타 적용*: 실데이터 빠름 ↔ 사용자 경험 리스크 → shadow 다음 단계로 미룸.
  - *강사 라벨링*: 저비용 ↔ 성과 아닌 정책 품질만 → 보조로만.
- **시행착오:** dry-run이 `report_shadow_decisions`를 import하다 그 모듈이 끌어온 infra(pymysql)
  때문에 DB 없이 안 돌았다. → 포맷팅을 `domain/shadow_report.py`의 순수 함수로 빼서 infra 의존을
  끊음. 그 과정에 `api.py`가 use case를 3개 인자로만 생성하던 **선존 버그**도 발견·수정.
- **결과 → 의미:** 실사용자 0명에서도 **파이프라인(계산→로깅→집계)이 돈다는 걸 dry-run 60명으로
  검증.** → **"0명을 0 아니게" 만드는 최저위험 경로 확보.** 단 합성 dry-run은 Phase 0~2의 정책
  변화량을 재생산할 뿐 **새 증거는 아니다** — 성과·잠재변수 판별은 여전히 실사용자 필요.

---

## 관통 서사 — "0.5 방어"에서 "왜 못 고르는지 안다"로

이 프로젝트의 진짜 결과는 개별 숫자가 아니라 **편향을 반복적으로 발견하고 제거한 궤적**이다.
- Phase 0: baseline 0.5가 무의미 구간을 0.5 근처로 만든 **측정 편향** 발견·제거.
- Phase 1: 방향 중립 배터리가 그 0.5를 탈락시킴 — 편향 제거가 작동함을 확인.
- Phase 2: minimax가 뱉은 0.2를 새 정답으로 밀 뻔한 **재편향**을 잡음.
- 결론: **"고정 stretch_factor를 오프라인만으로 정당화하는 접근 자체가 틀렸다"** — 어떤 값도
  추천하지 않고, 대신 shadow mode로 저위험 실측을 시작한다.

한 줄: **오프라인으로 갈 수 있는 끝은 "좋은 값을 고르는 것"이 아니라 "왜 오프라인으론 못 고르는지를
정직하게 알고, 그 위에서 저위험으로 실측을 시작하는 것"이었다.**

## 산출물 인덱스
| 종류 | 파일 |
|---|---|
| 분석 | `scripts/analyze_stretch_factor_decision_sensitivity.py` (P0), `redteam_stretch_factor_battery.py` (P1), `montecarlo_stretch_factor_minimax.py` (P2) |
| 폐기 | `scripts/simulate_stretch_factor_ab_test.py` (순환논증, deprecated) |
| 운영 | `application/use_cases.py`(shadow), `domain/shadow_report.py`, `scripts/report_shadow_decisions.py`, `scripts/dryrun_shadow_pipeline.py`, `GET /admin/shadow-summary` |
| 테스트 | `tests/test_stretch_factor_redteam.py`, `tests/test_shadow_mode.py`, `tests/test_shadow_report.py` (전체 43 통과) |
| 문서 | `problem_definition_*`, `roadmap_*`, `stretch_factor_onepager.md`, 본 회고 |
