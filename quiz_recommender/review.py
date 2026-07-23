"""복습 세트 선정 — '무엇을 복습할지'(원문제)까지 추천기가 고른다.

기존 recommender.get_similar_problems 는 원문제가 이미 주어진 상태에서 유사문제만 골랐다.
이 모듈은 그 앞 단계다: 학생 풀이 이력에서 원문제 자체를 선정한다(정책 '안 B').
역할 분담 — 종준 FSRS 는 타이밍(언제 뿌릴지)만, 무엇을 복습할지는 여기서 결정한다.

선정 규칙(확정):
  ① 섹션별 tier + 난이도별 '느림' 판정 (personalize 재사용)
  ② 복습 후보 = 이미 푼 문제 중 (틀림+느림 > 틀림 > 맞음+느림). 맞음+빠름(완전습득) 제외
  ③ 급한 순 정렬: 1차 섹션 tier(낮을수록 급함) · 2차 문제 우선순위 · 3차 오래 걸린 순
  ④ 섹션당 원문제 최대 MAX_PER_SECTION, 전체 최대 MAX_TOTAL 까지
  ⑤ 각 원문제마다 recommender.get_similar_problems 로 유사문제 k개
반환: [{"problem_id", "section_id", "similar": [...]}, ...]  (급한 순)

콜드스타트(이력 없음)면 빈 리스트 — 복습시킬 근거가 없다.
"""
try:
    from . import db, personalize, recommender
except ImportError:
    import db
    import personalize
    import recommender

# 복습 분량 상한 (확정): 섹션당 원문제 최대 2개, 전체 원문제 최대 10개.
MAX_ORIGINALS_PER_SECTION = 2
MAX_ORIGINALS_TOTAL = 10


def recommend_review(student_id: int, k: int = 2) -> list[dict]:
    """학생의 복습 세트를 급한 순으로 반환. 원문제 선정 + 유사문제 부착."""
    rounds = db.get_answer_rounds(student_id)
    if not rounds:
        return []  # 콜드스타트 — 복습 근거 없음

    originals = _select_originals(rounds)  # [(problem_id, section_id), ...] 급한 순

    review_set: list[dict] = []
    for problem_id, section_id in originals:
        recommended = recommender.get_similar_problems(student_id, problem_id, k)
        similar = recommended[1:] if recommended else []  # [0]=원문제 제외, 유사만
        meta = personalize._meta_of(problem_id)  # 유사문제는 원문제와 같은 코스(course 격리)
        review_set.append({
            "problem_id": problem_id,
            "section_id": section_id,
            "course_id": meta["courseId"] if meta else None,  # 백엔드가 그룹별 SimilarQuiz 저장에 사용
            "similar": similar,
        })
    return review_set


def _select_originals(rounds: list[dict]) -> list[tuple[int, int]]:
    """이력에서 원문제를 급한 순으로 선정. [(problem_id, section_id), ...].

    정렬 키 = (섹션 tier, 문제 우선순위, -풀이시간).
      · 섹션 tier: personalize._history_signals — 낮을수록 급한 섹션
      · 문제 우선순위: 0=틀림+느림 · 1=틀림 · 2=맞음+느림  (맞음+빠름은 후보 제외)
      · 풀이시간: 오래 걸린 순(내림차순 → 음수로 오름차순 정렬)
    상한: 섹션당 MAX_PER_SECTION, 전체 MAX_TOTAL.
    한 문제가 여러 번 풀렸으면 가장 급한 기록(우선순위 min, 그 안에서 시간 max)으로 대표.
    """
    section_tier, _mastered = personalize._history_signals(rounds)

    # 문제별로 가장 급한 기록만 남긴다: {qid: (section_id, priority, time)}
    best: dict[int, tuple[int, int, float]] = {}
    for rd in rounds:
        sec = rd["section_id"]
        times = rd.get("times", {})
        for qid, ok in rd["answers"]:
            t = times.get(qid)
            slow = personalize._is_slow(qid, t)   # 난이도별 목표시간 초과 여부
            if ok and not slow:
                continue  # 맞음+빠름 = 완전 습득 → 원문제 후보 아님
            priority = 0 if (not ok and slow) else 1 if not ok else 2  # 틀림+느림 / 틀림 / 맞음+느림
            time_val = t if t is not None else -1.0  # 시간 없으면 맨 뒤로(-1)
            cur = best.get(qid)
            # 더 급한 기록(우선순위 작음, 동률이면 시간 큼)으로 갱신
            if cur is None or (priority, -time_val) < (cur[1], -cur[2]):
                best[qid] = (sec, priority, time_val)

    # 정렬: 섹션 tier ↑ → 문제 우선순위 ↑ → 시간 ↓
    ordered = sorted(
        best.items(),
        key=lambda kv: (section_tier.get(kv[1][0], 3), kv[1][1], -kv[1][2]),
    )

    # 상한 적용: 섹션당 MAX_PER_SECTION, 전체 MAX_TOTAL
    per_section: dict[int, int] = {}
    result: list[tuple[int, int]] = []
    for qid, (sec, _pri, _t) in ordered:
        if len(result) >= MAX_ORIGINALS_TOTAL:
            break
        if per_section.get(sec, 0) >= MAX_ORIGINALS_PER_SECTION:
            continue
        per_section[sec] = per_section.get(sec, 0) + 1
        result.append((qid, sec))
    return result
