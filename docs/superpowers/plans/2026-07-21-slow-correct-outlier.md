# 느린 정답 이상치 → 복습 추천 반영 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 추천기가 "느린 정답 전부"가 아니라 "제출당 이상치(본인 이력 중앙값×1.5 초과) 최고 1개"만 복습 후보로 남기게 한다.

**Architecture:** `quiz_recommender/personalize.py` 한 파일. 신규 순수 함수 `slow_outlier_qids`로 제출당 top-1 이상치를 뽑고, `_history_signals`가 정답의 slow 판정을 그 집합으로 대체한다. 새 저장·스키마 없음(읽기 전용 계산). 복습 일정(FSRS)은 건드리지 않는다.

**Tech Stack:** Python 3, pytest. 의존성 추가 없음.

## Global Constraints

- 대상 파일: `quiz_recommender/personalize.py` **한 곳만**. FSRS/스케줄러 코드(`domain/`·`application/`·`infrastructure/`) 무변경.
- `time_spent_seconds` 계약: null만 미측정(후보 제외), 0은 유효값. 판정은 항상 `t is not None`.
- 느림 임계선은 기존 `_slow_threshold`(중앙값×1.5, 표본<3 또는 중앙값≤0이면 None) 규약 그대로.
- 브랜치 `feat/slow-correct-review`에 커밋 (스펙과 같은 브랜치).
- 기존 추천기 테스트 27개는 계속 통과해야 함(회귀).

---

### Task 1: `slow_outlier_qids` 순수 함수

**Files:**
- Modify: `quiz_recommender/personalize.py` (신규 함수 추가 — `_history_signals` 정의 바로 위, 현재 L105 근처)
- Test: `quiz_recommender/test_recommender.py` (신규 테스트 추가)

**Interfaces:**
- Consumes: 없음 (rounds 구조는 기존 `get_answer_rounds` 산출물 — `{"section_id": int, "answers": [(qid, ok)], "times": {qid: 초|None}}`)
- Produces: `slow_outlier_qids(rounds: list[dict], threshold: float | None) -> set[int]` — 각 라운드에서 **정답**이며 시간 ≥ threshold 인 답 중 시간 최고 1개 qid의 합집합. threshold=None이면 빈 집합.

- [ ] **Step 1: 실패하는 테스트 작성**

`quiz_recommender/test_recommender.py` 맨 끝에 추가:

```python
def test_slow_outlier_picks_single_highest_correct():
    """정답 중 threshold 넘는 것들에서 시간 최고 1개만. 오답은 후보 아님."""
    rounds = [{"section_id": 11, "answers": [(301, True), (302, True), (5, False)],
               "times": {301: 90, 302: 60, 5: 200}}]
    # 정답 중 15 초과: 301(90)·302(60) → 최고 301. 5는 오답이라 200이어도 제외.
    assert personalize.slow_outlier_qids(rounds, 15.0) == {301}


def test_slow_outlier_none_when_no_correct_over_threshold():
    rounds = [{"section_id": 11, "answers": [(301, True), (302, True)],
               "times": {301: 10, 302: 12}}]
    assert personalize.slow_outlier_qids(rounds, 15.0) == set()


def test_slow_outlier_empty_when_threshold_none():
    rounds = [{"section_id": 11, "answers": [(301, True)], "times": {301: 90}}]
    assert personalize.slow_outlier_qids(rounds, None) == set()


def test_slow_outlier_ignores_null_time():
    """시간 미측정(null) 정답은 이상치 후보에서 제외. 0은 유효값이지만 threshold 미만."""
    rounds = [{"section_id": 11, "answers": [(301, True), (302, True)],
               "times": {301: None, 302: 40}}]
    assert personalize.slow_outlier_qids(rounds, 15.0) == {302}


def test_slow_outlier_one_per_round():
    """제출(라운드)마다 최고 1개씩."""
    rounds = [
        {"section_id": 11, "answers": [(301, True), (302, True)], "times": {301: 90, 302: 60}},
        {"section_id": 22, "answers": [(303, True), (304, True)], "times": {303: 40, 304: 80}},
    ]
    assert personalize.slow_outlier_qids(rounds, 15.0) == {301, 304}
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd quiz_recommender && python -m pytest test_recommender.py -k slow_outlier -v`
Expected: FAIL — `AttributeError: module 'personalize' has no attribute 'slow_outlier_qids'`

- [ ] **Step 3: 최소 구현 추가**

`quiz_recommender/personalize.py`에서 `def _history_signals(` 정의 바로 위에 추가:

```python
def slow_outlier_qids(rounds: list[dict], threshold: float | None) -> set[int]:
    """각 라운드에서 정답이며 시간 ≥ threshold 인 답 중 시간이 가장 큰 1개 qid의 합집합.

    '느린 정답 이상치' — 맞았어도 본인 평소(중앙값×1.5)보다 확 느린 최고 1문제만 복습 대상으로
    남긴다. threshold=None(표본 부족/퇴화)이면 빈 집합. 시간 미측정(None) 답은 후보에서 제외(0은 유효값).
    """
    if threshold is None:
        return set()
    outliers: set[int] = set()
    for rd in rounds:
        times = rd.get("times", {})
        candidates = [
            (times[qid], qid)
            for qid, ok in rd["answers"]
            if ok and times.get(qid) is not None and times[qid] >= threshold
        ]
        if candidates:
            outliers.add(max(candidates)[1])
    return outliers
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd quiz_recommender && python -m pytest test_recommender.py -k slow_outlier -v`
Expected: PASS (5개)

- [ ] **Step 5: 커밋**

```bash
git add quiz_recommender/personalize.py quiz_recommender/test_recommender.py
git commit -m "feat(recommender): slow_outlier_qids — 제출당 느린 정답 이상치 top-1"
```

---

### Task 2: `_history_signals`가 이상치 집합을 쓰게 변경

**Files:**
- Modify: `quiz_recommender/personalize.py` (`_history_signals`, 현재 L105-126)
- Test: `quiz_recommender/test_recommender.py` (신규 테스트 추가)

**Interfaces:**
- Consumes: `slow_outlier_qids(rounds, threshold)` (Task 1)
- Produces: `_history_signals(rounds, threshold) -> (tiers: dict[int,int], mastered: set[int])` — 시그니처 불변. 동작만 변경: **정답의 slow 판정 = 이상치 집합 소속 여부**. 오답의 tier 0/1 판정은 기존(per-answer threshold) 유지.

- [ ] **Step 1: 실패하는 테스트 작성**

`quiz_recommender/test_recommender.py` 맨 끝에 추가:

```python
def test_history_signals_only_top_outlier_stays_reviewable():
    """정답 둘 다 느려도, 최고 이상치(301)만 복습 대상. 나머지 정답(302)은 습득 처리."""
    rounds = [{"section_id": 11, "answers": [(301, True), (302, True)],
               "times": {301: 90, 302: 60}}]
    tiers, mastered = personalize._history_signals(rounds, 15.0)
    assert 301 not in mastered, "이상치 정답은 습득에서 빠져 복습 대상으로 남아야 함"
    assert 302 in mastered, "이상치 아닌 정답은(느려도) 습득 처리돼 제외"


def test_history_signals_wrong_answer_slow_unchanged():
    """오답의 느림 판정(tier 0)은 이상치 정책과 무관하게 기존대로 동작."""
    rounds = [{"section_id": 22, "answers": [(3, False)], "times": {3: 90}}]
    tiers, mastered = personalize._history_signals(rounds, 15.0)
    assert tiers.get(22) == 0, "틀림+느림 단원은 tier 0"
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd quiz_recommender && python -m pytest test_recommender.py -k history_signals -v`
Expected: FAIL — `test_history_signals_only_top_outlier_stays_reviewable`에서 `302 in mastered` 실패 (현재는 302도 느려서 correct_slow에 들어가 mastered에서 빠짐)

- [ ] **Step 3: `_history_signals` 구현 교체**

`quiz_recommender/personalize.py`의 `_history_signals` 본문을 아래로 교체 (docstring 유지, 루프 내부 slow 판정만 변경):

```python
def _history_signals(rounds: list[dict], threshold: float | None) -> tuple[dict, set]:
    """이력 → (단원별 우선순위 tier, 완전습득 문제 집합).

    tier(작을수록 먼저): 0=틀림+느림, 1=틀림, 2=맞음+느림(이상치), 3=무신호.
    mastered: 맞힌 적 있고 한 번도 '느린 정답 이상치'였던 적 없는 문제 — 후보에서 제외.
    threshold=None(시간 미측정)이면 이상치 0 + 오답 tier 0이 안 나와 기존 동작과 동일해진다.
    """
    outliers = slow_outlier_qids(rounds, threshold)
    tiers: dict[int, int] = {}
    correct_fast: set[int] = set()
    correct_slow: set[int] = set()
    for rd in rounds:
        times = rd.get("times", {})
        for qid, ok in rd["answers"]:
            if ok:
                slow = qid in outliers                       # 정답: 이상치만 '느림'
                (correct_slow if slow else correct_fast).add(qid)
            else:
                t = times.get(qid)
                slow = threshold is not None and t is not None and t >= threshold  # 오답: 기존 판정
            tier = (0 if slow else 1) if not ok else (2 if slow else 3)
            sec = rd["section_id"]
            if tier < 3 and sec is not None:   # section 미상 이력이 미인덱싱 후보를 부스트하지 않게
                tiers[sec] = min(tiers.get(sec, 3), tier)
    return tiers, correct_fast - correct_slow
```

- [ ] **Step 4: 신규 테스트 통과 확인**

Run: `cd quiz_recommender && python -m pytest test_recommender.py -k history_signals -v`
Expected: PASS (2개)

- [ ] **Step 5: 전체 회귀 확인**

Run: `cd quiz_recommender && python -m pytest test_recommender.py -v`
Expected: 전체 PASS (기존 27개 + 신규 7개 = 34개). 특히 `test_correct_but_slow_not_excluded`(단일 이상치 301) PASS, `test_correct_and_fast_excluded` PASS.

- [ ] **Step 6: 커밋**

```bash
git add quiz_recommender/personalize.py quiz_recommender/test_recommender.py
git commit -m "feat(recommender): 느린 정답 '전부'→'이상치 top-1'만 복습 대상 (_history_signals)"
```

---

## Follow-up (코드 아님 — 발표 준비)

- **eval 재측정(오프라인):** 이 변경 후 추천 결과가 바뀌므로 발표 숫자 다시 측정. **측정 데이터에 문항별 시간이 있어야** 이상치 효과가 드러남(시간 없으면 변화 0). eval 시나리오(eval_labels류)에 시간을 넣어 오프라인 측정 — 배포 불필요.
- **배포:** 라이브 시연 없음(발표=숫자만) → personalize.py는 발표 후 배포해도 무방.
- **종준:** 이 작업은 FSRS 무변경. 종준에겐 "`similar_quiz_submission` 테이블 생김(답안별 정오답+시간)" 정보만 공유(작업 요청 아님).

## Self-Review

- 스펙 §3(변경 상세) 두 항목 = Task 1(`slow_outlier_qids`) + Task 2(`_history_signals`) 커버. ✓
- 스펙 §5 엣지(표본<3/중앙값≤0→None, null 시간, 정답 없음)= Task1 test_empty_when_threshold_none·ignores_null_time + 회귀 test_zero_median 커버. ✓
- 타입 일관성: `slow_outlier_qids(rounds, threshold)->set[int]` Task1 정의 = Task2 소비 일치. ✓
- 플레이스홀더 없음. 모든 스텝에 실제 코드·명령·기대출력 명시. ✓
