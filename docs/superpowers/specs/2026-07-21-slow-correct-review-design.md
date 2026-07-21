# 느린 정답 이상치 → 복습 대상 (FSRS + 추천기) 설계

**날짜:** 2026-07-21
**대상 레포:** Python-Server (`quiz_recommender/` + 스케줄러 앱 `domain/·application/·infrastructure/·presentation/`)
**선행 의존:** `db.get_answer_rounds`가 정규+유사 제출을 병합해 읽음 (PR #31, 머지 전). 이 설계는 그 위에 얹힘.

## 1. 배경 / 목표

FSRS 복습 스케줄러(종준)는 지금 **퀴즈 점수%만** 보고 다음 복습일을 계산한다 (`domain/review.py`). 그래서:

- 맞았지만 **오래 걸린** 정답도 "잘 앎(Easy/Good)"으로 처리 → 복습이 멀리 밀림.
- 유사퀴즈(복습) 제출을 **아예 안 읽음** (`MySQLQuizScoreRepository`가 `quiz_submission`만 조회) → 복습을 풀어도 review_card가 안 갱신됨.

목표: **맞았어도 그 학생 평소보다 확 느린(이상치) 정답은 복습 대상으로 되살린다.** 이 신호를 두 소비자에 일관되게 반영한다 — (a) 추천기(무엇을 추천), (b) FSRS 스케줄러(언제 다시).

## 2. 정책 정의 — "느린 정답 이상치"

한 제출(= 한 라운드) 안에서:

1. **임계선 T = 학생 전체 이력 풀이시간 중앙값 × 1.5** (`SLOW_FACTOR=1.5`).
   - 표본이 `MIN_TIME_SAMPLES(3)` 미만이거나 중앙값 ≤ 0 → `T=None`(신호 끔). 기존 `_slow_threshold` 규약 그대로.
   - 임계선은 **5문제가 아니라 학생 전체 이력**에서 나온다 → 통계적으로 안정, "본인 평소보다 확 느림"이라는 이상치 개념과 일치.
2. **이상치 선정:** 그 제출의 **정답**들 중 시간이 `T` 이상인 것들에서 **시간이 가장 큰 1개**만 "느린 정답 이상치"로 표시.
3. **제외:** `T`를 넘는 정답이 하나도 없으면 → 이 제출에서 느림 사유로 복습에 추가되는 문제 없음. (맞았고 이상치도 아니므로 습득으로 간주.)

> 핵심 변화: 기존 추천기는 임계선 넘는 정답을 **전부** 표시했다. 새 정책은 **제출당 최고 1개만**.

공유 순수 함수로 추출:

```python
# 각 라운드에서 정답 중 시간 ≥ threshold 인 것들의 최고 1개 qid.
# threshold=None 이면 빈 집합.
def slow_outlier_qids(rounds: list[dict], threshold: float | None) -> set[int]
```

추천기와 FSRS 파이프라인이 **같은 함수**를 쓴다 (느림 정의 단일화).

## 3. 변경 상세

### ① `quiz_recommender/personalize.py` — 추천기: "전부" → "이상치 top-1"

- 신규 `slow_outlier_qids(rounds, threshold)` 추가 (위 정의).
- `_history_signals`(L105) 수정: 지금은 `t >= threshold` 인 정답을 전부 `correct_slow`에 넣는다. 이를 **`slow_outlier_qids` 집합에 속한 정답만** `correct_slow`(tier 2)로 넣도록 변경. 나머지 정답은 `correct_fast`.
- `_slow_threshold`(L89)·`personalized_recommend`(L139, L155–156)는 시그니처 불변.

### ② `domain/review.py` — FSRS 등급에 slow 반영

- `review_lesson(card, quiz_score_percent, scheduler=None, review_datetime=None, max_interval_days=None, slow: bool = False)` — 인자 `slow` 추가.
- 점수로 등급 계산 후: `if slow: rating = min(rating, Rating.Hard)` (Again=1<Hard=2<Good=3<Easy=4). 맞았는데 느리면 Hard 상한, 틀린 것(Again)은 그대로.
- `quiz_score_to_grade`는 순수 유지(점수만). slow 캡은 `review_lesson` 안에서.

### ③ `infrastructure/repositories.py` — 유사퀴즈 제출 읽기 + slow 판정 소스

- `MySQLPendingReviewRepository`: 복습 대상 (enrollment, lesson)을 `quiz_submission` **뿐 아니라 `similar_quiz_submission`** 에서도 수집. 유사퀴즈 문항은 원래 `quiz_question` 이라 **기존 `lesson_quiz_map` 조인을 그대로 재사용**.
- `MySQLQuizScoreRepository`(또는 신규 메서드): (enrollment, lesson)의 최신 복습 이벤트 **점수%** 반환. 소스는 정규+유사 중 최신.
- slow 판정은 SQL이 아니라 Python에서: 학생 이력(`get_answer_rounds`) → `_slow_threshold` → `slow_outlier_qids`. 이상치 qid가 해당 lesson에 매핑되면 그 lesson의 복습 이벤트 `slow=True`.

### ④ `application/use_cases.py` — slow 플래그 배선

- `ReviewLessonUseCase.execute(enrollment_id, lesson_id)`:
  1. 점수 조회(기존).
  2. 학생 이력으로 threshold + `slow_outlier_qids` 계산(공유 함수). enrollment↔member 변환은 기존 어댑터(`ports.py:185`) 재사용.
  3. 이 lesson의 최신 복습 문항이 이상치 집합에 있으면 `slow=True`.
  4. `review_lesson(card, score, slow=slow, max_interval_days=...)` 호출.
- `UpdateDueReviewsUseCase`: 대상 목록에 유사퀴즈발 이벤트 포함(①③ 반영).

## 4. 데이터 흐름 (변경 후)

```
밤 배치(review_update.py)
 → 대상 (enrollment, lesson) 수집  [정규 + 유사 제출]        ← ③
 → 학생별 이력 로드 → threshold(중앙값×1.5) → 이상치 top-1 qid  ← ②(정책)/④
 → 각 lesson: score%로 등급, 이 lesson이 이상치 문항 포함하면 slow=True → Hard 상한  ← ②
 → review_card.due 저장
```

## 5. 적용 범위

- **정규 + 유사 퀴즈 둘 다** slow→Hard 적용 (사용자 확정). 정규 스케줄링 기존 동작이 바뀜(§7 리스크).
- 집계 단위 = **제출(라운드) 단위** top-1 이상치. 문항별 세분화 안 함.

## 6. 엣지 케이스

- 이력 표본 < 3 또는 중앙값 ≤ 0 → threshold=None → 이상치 0개 → 기존(점수만) 동작. 콜드스타트 안전.
- `time_spent_seconds`가 null(미측정)인 답은 이상치 후보에서 제외 (`t is not None`). 0은 유효값(FE ms→초 반올림). null만 미측정 계약 준수.
- 한 제출에 정답이 없거나 모두 threshold 미만 → 이상치 0개.
- 이상치 문항이 여러 lesson에 매핑(N:N) → 그 lesson들 모두 slow=True.

## 7. 영향 / 리스크

- **추천기 동작 변경:** "느린 정답 전부" → "제출당 이상치 1개". `correct_slow`/`mastered` 집합이 달라져 추천 결과가 바뀜 → **발표 eval 숫자 재측정 필요.**
- **정규 스케줄링 변경:** 정규 퀴즈 복습에도 slow→Hard가 적용되어, 종준의 기존 정규 due 계산이 바뀜. (유사퀴즈만으로 좁히려면 slow 판정을 유사 경로에서만 켜면 됨 — 플래그 하나.)
- **의존:** get_answer_rounds의 유사퀴즈 병합(PR #31)이 선행돼야 유사 제출이 이력·이상치에 반영됨.

## 8. 테스트

- `slow_outlier_qids`: 이상치 0/1/다수, threshold=None, null 시간 혼재, 0초 포함 케이스.
- `review_lesson(slow=True)`: 고득점+slow → Hard, 저득점(Again)+slow → Again 유지, slow=False → 기존.
- `_history_signals`: 이상치만 correct_slow에 들어가고 나머지 정답은 mastered로 빠지는지.
- 회귀: 기존 추천기 테스트 27개 + FSRS 테스트 통과.

## 9. 범위 밖

- FSRS 가중치 재학습(fsrs-optimizer), 소수점 초, 프론트 유휴감지 N(=5분 권고, FE 소관).
