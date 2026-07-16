# 정책 상수 전수조사

이 문서는 `domain/`·`application/`에 흩어져 있는 매직넘버(임계값·배율·계수)를 한곳에 모은 것.
목적은 "왜 이 값인지"와 "언제 다시 봐야 하는지"를 코드만 봐서는 알 수 없는 것들을 명시하는 것 -
값 자체를 정당화하려는 게 아니라, **각 값이 어느 카테고리인지**를 분명히 해서 잘못된 방식으로
검증하려 들지 않게 하는 게 핵심이다.

## 두 카테고리

- **[DATA]** 실측 데이터가 쌓이면 통계적으로 재계산/검증 가능한 값. "근거가 없다"는 지적이 정확히
  맞는 카테고리 - 지금은 synthetic 데이터나 직관으로만 잡혀 있고, 실측 파이프라인이 없으면 영원히
  임의값으로 남는다.
- **[POLICY]** 통계로 "정답"이 나오지 않는 제품/도메인 정책값. 실제 입시 서비스도 이런 건 강사·
  컨설턴트 판단으로 정하고, 이후 **완주율/성적 향상 같은 결과 지표**를 모니터링하면서 사람이 조정한다.
  "근거를 대라"는 요구 자체가 성립하지 않는 카테고리 - 대신 "어떤 지표를 보고 조정할지"가 명시돼야 한다.

## 목록

| 상수 | 위치 | 값 | 카테고리 | 근거 / 재검토 조건 |
|---|---|---|---|---|
| `SLIP_BUFFER_WEEKS` | domain/scheduler.py | 2 | POLICY | 학생이 정한 완주기간에서 최대 며칠 더 밀리는 걸 허용할지. 재검토: 실제 슬립 분포(학생들이 평균 몇 주씩 밀리는지) 나오면 이 값이 "대부분의 학생을 커버하는지" 확인. |
| `REVIEW_BUFFER_WEEKS` | domain/scheduler.py | 3 | POLICY | 수능 직전 새 진도 금지·총복습 기간. 사용자가 "3주(관습적 총정리 기간)"로 확정(2026-07-09). 재검토: 팀/컨설턴트 재논의 없이는 안 바뀜. |
| `PUSH_CAP_MULTIPLIER` | domain/reflow.py | 1.5 | POLICY | 평소 push_mode 강도 배율. 재검토: 이 배율로 밀린 학생이 실제로 따라잡는 비율(완주율)을 보고 조정. |
| `FINAL_STRETCH_DAYS` | domain/reflow.py | 100 | POLICY | 수능 D-100(입시 문화적으로 의미있는 시점). 재검토: 안 함(관습적 상징성이 근거라 데이터로 안 바뀜). |
| `FINAL_STRETCH_PUSH_CAP_MULTIPLIER` | domain/reflow.py | 1.8 | POLICY | D-100 이내 전반 강도. 사용자가 "평소보다 세게"로 확정, 1.8은 임의 선택. 재검토: D-100 진입 후 이탈률이 오히려 늘면(너무 세서 역효과) 낮추는 방향 검토. |
| `MIN_EFFICIENCY_SAMPLES` | domain/scheduler.py | 3 | DATA (미검증) | "3건이면 콜드스타트 벗어남"은 직관치. 재검토: 실측 리뷰 로그 쌓이면 샘플수별 raw 계수의 표본분산을 보고 "안정화되는 시점"을 다시 잡을 것 - `scripts/calibrate_risk_weights.py`류 스크립트로 확장 가능. |
| `MIN/MAX_EFFICIENCY_COEFFICIENT` | domain/scheduler.py | 0.5 / 2.0 | POLICY (안전장치) | 극단치 클램핑 - "통계적으로 옳은 범위"가 아니라 안전장치. 재검토 불필요(사고 방지용 상한/하한이라 넓혀도 되는 근거가 나오기 전까진 유지). |
| `EFFICIENCY_STRETCH_FACTOR` | domain/scheduler.py | 0.5(기본값, 실제로는 학생마다 실험 variant 적용) | **DATA (A/B 실험 진행 중)** | "절반만 반영"은 사용자 선택이지 실험 근거 없음. `domain/experiments.py::assign_variant()`가 학생마다 [0.3, 0.5, 0.7] 중 하나를 결정적으로 배정하고 `experiment_exposure` 테이블에 노출을 기록 중(배정 인프라는 완료). 남은 건 완주율/성적 향상 같은 성과 데이터와의 조인 - 그게 쌓이면 `scripts/calibrate_policy_constants.py::analyze_stretch_factor_ab_test()`를 채워서 그룹별 비교. |
| `DEFAULT_MAX_REVIEW_INTERVAL_DAYS` | application/use_cases.py | 180 | POLICY (폴백) | 수능일 미확인 시 폴백. 재검토: 온보딩 완료율이 높아져 이 폴백이 거의 안 쓰이면 우선순위 낮음. |
| risk 가중치 (recency/streak/quiz) | domain/risk.py | 0.45 / 0.30 / 0.25 | **DATA (교체 경로 있음)** | `scripts/coxph_synthetic_population.py`의 synthetic ground truth(0.9:0.6:0.4)를 정규화한 값 - **synthetic이지 실측 아님**. 재검토: 실제 이탈 이벤트가 쌓이면 `scripts/calibrate_risk_weights.py`로 실측 Cox PH 계수를 뽑아 이 상수와 비교, 드리프트 크면 교체(로드맵상 원래 "규칙기반→Cox PH 승급"으로 예정돼 있던 것). |
| `risk_label` 임계값 | domain/risk.py | 0.7 / 0.4 (HIGH/MEDIUM) | POLICY | 스코어 구간 나누기. 재검토: 실제 라벨별 실제 이탈률을 집계해서 "HIGH라고 한 학생이 진짜 더 많이 이탈하는지" 확인 후 조정. |
| 퀴즈점수→FSRS grade 임계값 | domain/review.py | 90/70/50 | POLICY | CLAUDE.md에 이미 명시: "실전에 정립된 공식 없음, 실측 쌓이면 조정 가능." |

## 남은 일

- [DATA] 표시된 항목 중 **risk 가중치**는 실측 이탈 데이터만 쌓이면 바로 재계산 가능한 스크립트가 있음(`scripts/calibrate_risk_weights.py`).
- **`EFFICIENCY_STRETCH_FACTOR`**는 A/B 테스트 없이는 원천적으로 검증 불가 - 실측 파이프라인을 만들어도 "관찰 데이터"만으로는 인과관계(스트레치를 세게 줬더니 점수가 올랐다)를 주장할 수 없음. 이건 나중에 실제 A/B 실험을 설계해야 하는 항목으로 별도 관리.

## `EFFICIENCY_STRETCH_FACTOR` 졸업 기준 (언제 "고정 상수"에서 "실측 비교"로 넘어가는지)

지금은 실사용자가 0명이라 검증 자체가 불가능한 상태 - 아래 조건을 **전부** 만족하기 전까지는
`scripts/calibrate_policy_constants.py::analyze_stretch_factor_ab_test()`를 실행해도 의미 없다.

1. **표본 조건**: variant([0.3, 0.5, 0.7]) 그룹마다 `experiment_exposure`에 노출된 학생이
   **각 그룹 최소 30명 이상**(risk 가중치 재검증 때 쓴 `MIN_REAL_EVENTS_FOR_CALIBRATION=30`과
   동일 기준 - 일관성 유지).
2. **기간 조건**: 그 학생들이 **최소 4주 이상** 활동한 뒤 - efficiency 계수 자체가 안정화(완료
   3건 이상)되는 데 보통 1~2주 걸리므로(이전 세션 분석 참고), 그 이후로도 결과가 누적될
   시간이 필요함.
3. **성과 지표 정의**: "완주율"과 "퀴즈 평균점수 추이(등록 시점 대비 4주 후 변화량)" 중
   최소 하나가 실제로 로깅되고 있어야 함 - 지금은 이 성과 집계 자체가 시스템에 없으므로,
   위 표본/기간 조건을 만족하기 전에 **이 로깅부터 먼저 만들어야** 분석이 가능함.

세 조건 중 하나라도 안 채워진 상태에서 나온 "0.5가 더 낫다/아니다" 같은 결론은 표본 부족으로
인한 노이즈일 뿐이니, 발표나 의사결정 근거로 쓰지 말 것.
