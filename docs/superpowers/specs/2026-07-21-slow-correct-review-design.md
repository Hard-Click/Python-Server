# 느린 정답 이상치 → 복습 추천 반영 (추천기 한정)

**날짜:** 2026-07-21
**대상 레포:** Python-Server — `quiz_recommender/personalize.py` **한 파일**
**선행 의존:** `db.get_answer_rounds`가 정규+유사 제출을 병합해 읽음 (PR #31, 머지 전).
**범위 축소 근거:** 복습 "일정(언제)"은 종준 FSRS AI의 영역이라 건드리지 않는다. 추천기는 복습 "내용(어떤 문제)"만 담당한다.

## 1. 배경 / 목표

복습을 풀 때 **맞았지만 그 학생 평소보다 확 느린(이상치) 정답**을, 다음 복습 추천에서 되살린다. 지금 추천기(`_history_signals`)는 임계선 넘는 느린 정답을 **전부** 복습 후보로 남기는데, 이를 **제출당 이상치 최고 1개**로 좁힌다.

**안 하는 것:** FSRS 복습날짜 변경, `similar_quiz_submission`을 FSRS가 읽게 하기, rating(slow→Hard) — 전부 종준 영역이라 이번 범위 밖.

## 2. 정책 — "느린 정답 이상치"

한 제출(= 한 라운드) 안에서:

1. **임계선 T = 학생 전체 이력 풀이시간 중앙값 × 1.5** (`SLOW_FACTOR=1.5`).
   - 표본 < `MIN_TIME_SAMPLES(3)` 또는 중앙값 ≤ 0 → `T=None`(신호 끔). 기존 `_slow_threshold` 규약 그대로.
   - 임계선이 5문제가 아니라 **학생 전체 이력**에서 나와 통계적으로 안정 = "본인 평소보다 확 느림".
2. **이상치 선정:** 그 제출의 **정답** 중 시간 ≥ `T` 인 것들에서 **시간이 가장 큰 1개**만 "느린 정답 이상치".
3. **제외:** `T` 넘는 정답이 없으면 → 그 제출에서 느림 사유로 남기는 문제 0개. (맞았고 이상치 아니면 습득으로 간주 → mastered.)

> 변화: 기존 "임계선 넘는 정답 전부" → 새 "제출당 이상치 top-1만".

## 3. 변경 상세 — `quiz_recommender/personalize.py` 한 곳

- **신규 순수 함수** `slow_outlier_qids(rounds, threshold) -> set[int]`
  - 각 라운드에서 정답 중 시간 ≥ threshold 인 것의 **최고 1개 qid**. `threshold=None`이면 빈 집합.
  - `time_spent_seconds`가 null인 답은 후보 제외(`t is not None`). 0은 유효값.
- **`_history_signals`(L105) 수정**
  - 현재: `t >= threshold` 인 정답을 전부 `correct_slow`(tier 2)에 넣음.
  - 변경: **`slow_outlier_qids` 집합에 속한 정답만** `correct_slow`. 나머지 정답은 `correct_fast`(→ mastered로 빠짐).
- `_slow_threshold`(L89)·`personalized_recommend`(L139, L155–156)·`_ladder_state`·`_difficulty_pair`는 **불변**.

## 4. 데이터 흐름 / 저장

- 추천 호출 시 `get_answer_rounds(student_id)`로 이력(정규+유사) 로드 → threshold → 이상치 판정 → tier/mastered → 재랭킹. **전부 계산, 저장 없음.**
- **새 컬럼·새 테이블 없음.** 사다리 상태·이상치 모두 이미 저장된 제출 이력(`quiz_submission`·`similar_quiz_submission` + 답안)에서 파생. 상태는 어디에도 영속화하지 않는다(단일 소스 = 제출 이력).

## 5. 엣지 케이스

- 이력 표본 < 3 또는 중앙값 ≤ 0 → threshold=None → 이상치 0 → 기존 동작. 콜드스타트 안전.
- null 시간 답 → 이상치 후보 제외. 0초는 유효값(FE ms→초 반올림, null만 미측정 계약).
- 한 제출에 정답 없음 / 전부 threshold 미만 → 이상치 0.

## 6. 영향 / 리스크

- **추천 결과 변경:** `correct_slow`/`mastered` 집합이 달라짐 → **발표 eval 재측정 필요.**
- **종준 FSRS·복습일정·정규 스케줄: 무변경(리스크 0).**
- **의존:** get_answer_rounds 유사 병합(PR #31)이 선행돼야 유사 복습의 느린 정답이 이상치 판정에 반영됨. 미머지 상태에선 정규 이력만으로 동작(무해).

## 7. 테스트

- `slow_outlier_qids`: 이상치 0/1/다수, threshold=None, null·0초 혼재.
- `_history_signals`: 이상치만 `correct_slow`에 들어가고 나머지 정답은 mastered로 빠지는지.
- 회귀: 기존 추천기 테스트 27개 통과.

## 8. 범위 밖 (발표 후 여지)

- FSRS 복습일정에 느린 정답 반영(slow→Hard) — 종준 소관, 필요 시 종준이 결정.
- 사다리 상태를 종준에게 노출(`get_ladder_state` 등) — 종준이 스케줄에 난이도 진행을 쓰고 싶을 때만.
