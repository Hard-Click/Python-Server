"""추천 품질 평가 하네스 — 개인화 전/후를 숫자로 비교한다.

발표의 마지막 문장("개인화하니 X→Y")을 만들어주는 게이트 도구.
두 가지를 잰다:

  1) 관련성(relevance)   : 추천이 '진짜 유사한' 문제를 잘 집나        → recall@k, MRR
  2) 약점 적중(weak-hit) : 추천이 이 학생이 '틀린 개념(section)'을 때리나 → weak_hit@k

핵심 비교:
  · 베이스라인(유사도-only, 지금 방식)  vs  개인화(오답 기반)
  · 기대: weak_hit@k 는 크게 오르고, recall@k(관련성)는 유지된다
    → "약점은 더 맞히면서 관련성은 안 깎았다"

라벨:  eval_labels.json   (eval_labels.template.json 을 복사해 20~30개 채운다)
선행:  seed 적재 완료 + .env(QDRANT_URL/KEY). 이 스크립트는 Qdrant 검색만 하므로
       임베딩 키 없이도 돈다(적재만 돼 있으면).
실행:  .venv\\Scripts\\python.exe eval_metrics.py
"""
import json
from collections import Counter
from pathlib import Path

try:
    from . import vector_store
except ImportError:
    import vector_store


# ─────────────────────────── 지표 ───────────────────────────
def recall_at_k(recs: list[int], relevant: list[int], k: int) -> float | None:
    """추천 top-k 중 정답(유사) 문제를 얼마나 회수했나. 정답 없으면 None(집계 제외)."""
    if not relevant:
        return None
    hit = len(set(recs[:k]) & set(relevant))
    return hit / min(k, len(relevant))


def precision_at_k(recs: list[int], relevant: list[int], k: int) -> float | None:
    if k == 0:
        return None
    return len(set(recs[:k]) & set(relevant)) / k


def mrr(recs: list[int], relevant: list[int]) -> float:
    """첫 정답의 역순위. 정답이 위에 올수록 1에 가깝다."""
    rel = set(relevant)
    for i, r in enumerate(recs, start=1):
        if r in rel:
            return 1.0 / i
    return 0.0


def weak_hit_at_k(recs: list[int], weak_sections: list[int], section_of, k: int) -> float | None:
    """추천 top-k 중 학생 약점 section에 속한 비율. 약점 없으면 None(집계 제외)."""
    if not weak_sections or not recs:
        return None
    weak = set(weak_sections)
    top = recs[:k]
    hit = sum(1 for r in top if section_of(r) in weak)
    return hit / len(top)


def solved_leak_at_k(recs: list[int], solved_correct: list[int], k: int) -> float | None:
    """추천 top-k 중 학생이 '이미 맞힌' 문제가 낀 비율 — 복습에선 낮을수록 좋다.
    맞힌 문제가 없는 학생이면 None(샐 것 자체가 없음 → 집계 제외)."""
    if not solved_correct or not recs:
        return None
    solved = set(solved_correct)
    top = recs[:k]
    return sum(1 for r in top if r in solved) / len(top)


def fresh_recall_at_k(recs: list[int], relevant: list[int], solved_correct: list[int], k: int) -> float | None:
    """'아직 못 푼' relevant 만 정답으로 치는 recall — 복습 관점의 공정한 관련성.
    (이미 맞힌 문제는 다시 추천해도 복습 가치가 없으므로 정답에서 뺀다.)"""
    fresh = [r for r in relevant if r not in set(solved_correct)]
    return recall_at_k(recs, fresh, k)


def difficulty_fit_at_k(recs: list[int], expected_pair, difficulty_of, k: int) -> float | None:
    """추천 top-k 의 난이도가 사다리가 의도한 난이도 쌍과 일치한 비율.
    쌍과 실제 난이도를 멀티셋으로 비교(순서 무관). 사다리 판정 불가면 None."""
    if expected_pair is None or not recs:
        return None
    top = recs[:k]
    want = Counter(expected_pair[:len(top)])
    got = Counter(difficulty_of(r) for r in top)
    hit = sum(min(cnt, got[d]) for d, cnt in want.items())
    return hit / len(top)


# ─────────────────── 추천기 어댑터 ───────────────────
def baseline_recommend(student_id: int, query_id: int, course_id, k: int) -> list[int]:
    """유사도-only (현재 방식). student_id 를 무시한다 = 개인화 전.
    원문제(query_id) 자신은 제외하고 유사 top-k 만 반환."""
    return vector_store.search(query_id, {"courseId": course_id}, {query_id}, limit=k)


def make_section_lookup():
    """question_id → sectionId. Qdrant payload 에서 읽고 캐시한다."""
    cache: dict[int, int | None] = {}

    def section_of(qid: int):
        if qid not in cache:
            meta = vector_store.retrieve_meta(qid)
            cache[qid] = meta["sectionId"] if meta else None
        return cache[qid]

    return section_of


def make_difficulty_lookup():
    """question_id → difficulty. Qdrant payload 에서 읽고 캐시한다."""
    cache: dict[int, int | None] = {}

    def difficulty_of(qid: int):
        if qid not in cache:
            meta = vector_store.retrieve_meta(qid)
            cache[qid] = meta["difficulty"] if meta else None
        return cache[qid]

    return difficulty_of


def make_expected_pair_fn():
    """(student_id, query_id) → 사다리가 의도한 난이도 쌍. personalize 의 상태머신을
    그대로 재사용해 계산한다 — baseline/personalized 를 같은 기준으로 채점하기 위함.
    personalize 미구현이면 항상 None(difficulty_fit 집계 제외)."""
    try:
        from personalize import _difficulty_pair, _ladder_state, _meta_of, db as _pdb
    except ImportError:
        return lambda sid, qid: None

    def expected(sid: int, qid: int):
        meta = _meta_of(qid)
        if not meta or meta["difficulty"] is None:
            return None
        rounds = _pdb.get_answer_rounds(sid)
        section_rounds = [
            rd for rd in rounds
            if rd["section_id"] == meta["sectionId"] and all(q != qid for q, _ in rd["answers"])
        ]
        return _difficulty_pair(_ladder_state(section_rounds), meta["difficulty"])

    return expected


# ─────────────────── 하네스 ───────────────────
def _avg(vals: list) -> float | None:
    xs = [v for v in vals if v is not None]
    return sum(xs) / len(xs) if xs else None


def _load_labels(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    # JSON 키는 문자열 → int 로 정규화
    data["queries"] = {int(q): v for q, v in data["queries"].items()}
    data["students"] = {int(s): v for s, v in data["students"].items()}
    return data


def evaluate(recommend_fn, labels: dict, section_of, difficulty_of=None, expected_pair_fn=None) -> dict:
    """모든 시나리오(학생×원문제)에 recommend_fn 을 돌려 지표를 집계."""
    k = labels["k"]
    queries, students = labels["queries"], labels["students"]
    per = {"recall": [], "precision": [], "mrr": [], "weak_hit": [],
           "fresh_recall": [], "solved_leak": [], "difficulty_fit": []}

    for sc in labels["scenarios"]:
        q = queries[sc["query_id"]]
        st = students[sc["student_id"]]
        solved = st.get("solved_correct_ids", [])
        recs = recommend_fn(sc["student_id"], sc["query_id"], q["course_id"], k)

        per["recall"].append(recall_at_k(recs, q["relevant_ids"], k))
        per["precision"].append(precision_at_k(recs, q["relevant_ids"], k))
        per["mrr"].append(mrr(recs, q["relevant_ids"]))
        per["weak_hit"].append(weak_hit_at_k(recs, st.get("weak_sections", []), section_of, k))
        per["fresh_recall"].append(fresh_recall_at_k(recs, q["relevant_ids"], solved, k))
        per["solved_leak"].append(solved_leak_at_k(recs, solved, k))
        if difficulty_of is not None and expected_pair_fn is not None:
            pair = expected_pair_fn(sc["student_id"], sc["query_id"])
            per["difficulty_fit"].append(difficulty_fit_at_k(recs, pair, difficulty_of, k))
        else:
            per["difficulty_fit"].append(None)

    return {m: _avg(v) for m, v in per.items()}


def _fmt(x) -> str:
    return "  n/a" if x is None else f"{x:5.1%}"


def report(results: dict[str, dict]) -> None:
    """{이름: 지표} 를 표로 출력. 개인화 전/후를 나란히 본다."""
    metrics = ["recall", "precision", "mrr", "weak_hit", "fresh_recall", "solved_leak", "difficulty_fit"]
    names = list(results)
    print(f"\n{'metric':<14}" + "".join(f"{n:>14}" for n in names))
    print("-" * (14 + 14 * len(names)))
    for m in metrics:
        print(f"{m:<14}" + "".join(f"{_fmt(results[n].get(m)):>14}" for n in names))
    if len(names) == 2:
        a, b = names
        la, lb = results[a].get("solved_leak"), results[b].get("solved_leak")
        if la is not None and lb is not None:
            print(f"\n→ solved_leak@k: {la:.1%} ({a}) → {lb:.1%} ({b})  = 이미 푼 문제를 또 추천한 비율")
        fa, fb = results[a].get("fresh_recall"), results[b].get("fresh_recall")
        if fa is not None and fb is not None:
            print(f"→ fresh_recall@k: {fa:.1%} ({a}) → {fb:.1%} ({b})  = 복습 가치 있는 관련성")
        da, db_ = results[a].get("difficulty_fit"), results[b].get("difficulty_fit")
        if da is not None and db_ is not None:
            print(f"→ difficulty_fit@k: {da:.1%} ({a}) → {db_:.1%} ({b})  = 사다리가 의도한 난이도 적중")


def main() -> None:
    labels_path = Path(__file__).parent / "eval_labels.json"
    if not labels_path.exists():
        raise SystemExit(
            "eval_labels.json 이 없습니다. eval_labels.template.json 을 복사해 20~30개 채우세요."
        )
    labels = _load_labels(labels_path)
    section_of = make_section_lookup()
    difficulty_of = make_difficulty_lookup()
    expected_pair_fn = make_expected_pair_fn()  # personalize 미구현이면 None 반환 함수

    results = {"baseline": evaluate(baseline_recommend, labels, section_of, difficulty_of, expected_pair_fn)}

    try:
        from personalize import personalized_recommend  # 2주차 산출물
        results["personalized"] = evaluate(personalized_recommend, labels, section_of, difficulty_of, expected_pair_fn)
    except ImportError:
        print("[안내] personalize.personalized_recommend 미구현 → 베이스라인만 측정합니다.")

    report(results)


if __name__ == "__main__":
    main()
