"""개인화 추천기 (2주차 산출물).

베이스라인(유사도-only)과 달리 학생의 풀이 이력을 반영한다. 신호는 셋:
  · 비슷함: 같은 강의 안에서 임베딩 유사 후보를 넉넉히 뽑고,
  · 필요함: 학생이 약한 개념(section)을 위로, 이미 맞힌 문제는 빼고,
  · 수준:   난이도 사다리 — 직전 복습 라운드(제출 1건) 결과로 승급/강등하는
            상태머신. 난이도 1=하, 2=중, 3=상.

사다리 상태와 유사 2문제의 난이도 구성:
  BASE  (첫 복습)          → (원 난이도, 원+1)   수준 확인 + 살짝 도전
  TOP   (라운드 전부 정답) → (상, 상)            최고 난이도 도전
  MID   (TOP에서 틀림)     → (중, 상)            안전망 + 재도전 믹스
  FLOOR (MID/BASE에서 틀림)→ (원 난이도, 하)     기초 다지기
  전이: 라운드 전부 정답 → TOP / 틀림 → TOP은 MID로, 나머지는 FLOOR로.
  복습을 촉발한 라운드(원문제가 포함된 제출)는 판정에서 제외한다.
  원하는 난이도가 후보에 없으면 가장 가까운 난이도로 자연히 내려앉는다.

eval_metrics.py 가 baseline_recommend 와 동일한 시그니처로 호출한다:
    personalized_recommend(student_id, query_id, course_id, k) -> list[int]

원문제 난이도가 없으면(NULL) 사다리를 건너뛰고 약점 재랭킹만 한다.
"""
from functools import lru_cache

try:
    from . import db, vector_store
except ImportError:
    import db
    import vector_store

# 유사 후보를 k보다 넉넉히 뽑아야 난이도별로 고를 여지가 생긴다.
CANDIDATE_POOL = 20
MIN_DIFFICULTY = 1
MID_DIFFICULTY = 2
MAX_DIFFICULTY = 3


@lru_cache(maxsize=2048)
def _meta_of(qid: int) -> dict | None:
    """question_id → Qdrant payload(courseId/sectionId/difficulty). LRU 캐시(장기 실행 프로세스 대비 크기 상한)."""
    return vector_store.retrieve_meta(qid)


def _section_of(qid: int) -> int | None:
    meta = _meta_of(qid)
    return meta["sectionId"] if meta else None


def _difficulty_of(qid: int) -> int | None:
    meta = _meta_of(qid)
    return meta["difficulty"] if meta else None


def _ladder_state(section_rounds: list[dict]) -> str:
    """시간순 라운드를 걸으며 상태를 전이한다."""
    state = "BASE"
    for rd in section_rounds:
        if all(ok for _, ok in rd["answers"]):
            state = "TOP"
        else:
            state = "MID" if state == "TOP" else "FLOOR"
    return state


def _difficulty_pair(state: str, orig_diff: int) -> tuple[int, int]:
    """상태 → 유사 2문제의 목표 난이도 쌍."""
    if state == "TOP":
        return (MAX_DIFFICULTY, MAX_DIFFICULTY)
    if state == "MID":
        return (MID_DIFFICULTY, MAX_DIFFICULTY)
    if state == "FLOOR":
        return (orig_diff, MIN_DIFFICULTY)
    return (orig_diff, min(orig_diff + 1, MAX_DIFFICULTY))  # BASE(콜드스타트)


def _ranked(pool: list[int], weak: set, target: int | None) -> list[int]:
    """약한 section 우선 → 난이도가 target 에 가까운 순.
    난이도 미상(None)은 뒤로. 안정 정렬이라 동순위 안에서는 유사도순이 보존된다."""
    def key(qid: int):
        d = _difficulty_of(qid)
        dist = abs(d - target) if (d is not None and target is not None) else 9
        return (0 if _section_of(qid) in weak else 1, dist)
    return sorted(pool, key=key)


def personalized_recommend(student_id: int, query_id: int, course_id, k: int) -> list[int]:
    # 0) 이력부터 — 이력이 없으면(콜드스타트) 개인화 신호가 없으므로 베이스라인에 맡긴다.
    #    (recommender._recommend 가 빈 리스트를 받으면 섹션 필터가 걸린 폴백 사다리로 채움)
    rounds = db.get_answer_rounds(student_id)
    if not rounds:
        return []

    # 1) 비슷함: 같은 강의 안 유사 후보 (원문제 자신 제외, 강사 격리 필터 포함)
    meta = _meta_of(query_id)
    spec = {"courseId": course_id}
    if meta and meta.get("instructorId") is not None:
        spec["instructorId"] = meta["instructorId"]   # 강사 간 문제 공유 금지 정책
    candidates = vector_store.search(query_id, spec, {query_id}, limit=CANDIDATE_POOL)

    # 2) 필요함: 라운드 이력에서 오답/정답/약점 유도, 이미 맞힌 문제는 후보에서 제외
    correct = {qid for rd in rounds for qid, ok in rd["answers"] if ok}
    weak = {rd["section_id"] for rd in rounds for _, ok in rd["answers"] if not ok}
    pool = [c for c in candidates if c not in correct]

    # 3) 수준: 원문제 단원의 라운드들로 사다리 상태 판정 (촉발 라운드 제외)
    orig_diff = meta["difficulty"] if meta else None
    section = meta["sectionId"] if meta else None
    if orig_diff is None:
        return _ranked(pool, weak, None)[:k]  # 난이도 정보 없음 → 약점 재랭킹만

    section_rounds = [
        rd for rd in rounds
        if rd["section_id"] == section and all(qid != query_id for qid, _ in rd["answers"])
    ]
    d1, d2 = _difficulty_pair(_ladder_state(section_rounds), orig_diff)

    picked: list[int] = []
    for want in (d1, d2):
        for c in _ranked(pool, weak, want):
            if c not in picked:
                picked.append(c)
                break
    for c in _ranked(pool, weak, orig_diff):  # 슬롯이 남으면 원 난이도 근접순으로 채움
        if len(picked) >= k:
            break
        if c not in picked:
            picked.append(c)
    return picked[:k]
