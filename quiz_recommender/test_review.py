"""복습 세트 선정(review.py) 단위테스트 — 원문제 선정 규칙 + 글루 + 엔드포인트 배선.

test_recommender.py 와 동일하게, db/vector_store 는 config(env)·qdrant_client 를 끌어오므로
import 전에 가짜 모듈로 대체한다 → RDS·Qdrant·키 없이 '선정 규칙(계약)'만 순수 검증.
_select_originals 자체는 personalize 의 순수함수(_slow_threshold/_history_signals)만 쓰므로
이력(rounds)을 손으로 만들어 정렬·우선순위·상한·제외 규칙을 그대로 못박는다.
"""
import sys
import types

import pytest

# --- review import 전에 무거운 의존성을 가짜 모듈로 주입 ---
_fake_db = types.ModuleType("db")
_fake_db.get_answer_rounds = lambda sid: []   # 기본: 이력 없음(콜드스타트)
sys.modules["db"] = _fake_db
_fake_vs = types.ModuleType("vector_store")
sys.modules["vector_store"] = _fake_vs

import review        # noqa: E402  (가짜 주입 후 import)
import personalize   # noqa: E402


def _round(section_id, answers, times=None):
    """제출 1건(라운드). answers=[(qid, is_correct)], times={qid: 초}."""
    return {"section_id": section_id, "answers": answers, "times": times or {}}


# ---------- _select_originals: 선정 규칙 ----------

def test_coldstart_no_history_selects_nothing():
    """이력 없음 → 복습시킬 근거 없음 → 빈 선정."""
    assert review._select_originals([]) == []


def test_wrong_slow_ranks_before_wrong_fast_and_masters_excluded():
    """한 섹션 안: 틀림+느림(우선순위0)이 틀림+빠름(1)보다 앞. 맞음+빠름은 후보 제외."""
    # times median=1 → 느림 임계값 1.5. q1@10=느림, q2/q3@1=빠름.
    rounds = [_round(100, [(1, False), (2, False), (3, True)], {1: 10, 2: 1, 3: 1})]
    result = review._select_originals(rounds)
    assert result == [(1, 100), (2, 100)]        # 느린 오답이 먼저, 그다음 빠른 오답
    assert 3 not in [qid for qid, _ in result]   # 맞음+빠름(완전습득)은 빠짐


def test_correct_slow_is_kept_but_lower_priority_than_wrong_section():
    """'맞았지만 느림'은 제외되지 않고 남되(우선순위2), 틀린 섹션(tier↓)보다 뒤로 밀린다."""
    # times [2,2,10] median=2 → 임계값 3. q2@10=느림.
    rounds = [
        _round(200, [(1, False), (3, False)], {1: 2, 3: 2}),  # 틀림+빠름 → 섹션 tier 1
        _round(201, [(2, True)], {2: 10}),                    # 맞음+느림 → 섹션 tier 2
    ]
    result = review._select_originals(rounds)
    assert (2, 201) in result                    # 맞음+느림도 복습 대상으로 살아남음
    assert result[-1] == (2, 201)                # 단, 틀린 섹션 뒤로
    assert all(sec == 200 for _, sec in result[:2])


def test_cap_per_section_limits_to_two():
    """한 섹션에서 후보가 많아도 섹션당 MAX_ORIGINALS_PER_SECTION(2)까지만."""
    rounds = [_round(300, [(1, False), (2, False), (3, False)], {1: 5, 2: 5, 3: 5})]
    result = review._select_originals(rounds)
    assert len(result) == 2
    assert all(sec == 300 for _, sec in result)


def test_cap_total_limits_to_ten():
    """전체 후보가 넘쳐도 MAX_ORIGINALS_TOTAL(10)까지만(섹션 6개×2=12 → 10)."""
    rounds = []
    qid = 1
    times = {}
    for sec in range(400, 406):           # 6개 섹션
        answers = [(qid, False), (qid + 1, False)]
        times[qid] = 1
        times[qid + 1] = 1
        rounds.append(_round(sec, answers, {qid: 1, qid + 1: 1}))
        qid += 2
    result = review._select_originals(rounds)
    assert len(result) == review.MAX_ORIGINALS_TOTAL == 10


def test_synthetic_slow_correct_gets_selected_when_no_wrong_crowding():
    """[가상 시나리오] 실데이터(9231)는 틀린 섹션 7개가 top-10을 다 채워 '느린 정답'
    원문제가 선정까지는 못 갔다. 틀림이 자리를 안 뺏는 학생이면 '맞았지만 느림'이
    실제로 원문제로 뽑히는가? → 뽑힌다. (본인 median 낮게 유지: 대부분 빠른정답)."""
    # times median=1 → 임계값 1.5. @15만 느림. 빠른정답(@1)은 완전습득 → 제외.
    rounds = [
        _round(700, [(1, True), (3, True)], {1: 15, 3: 1}),   # q1 맞음+느림(target), q3 맞음+빠름(제외)
        _round(701, [(2, True), (4, True)], {2: 15, 4: 1}),   # q2 맞음+느림(target), q4 맞음+빠름(제외)
        _round(702, [(5, True), (6, True)], {5: 1, 6: 1}),    # 완전습득 섹션 → 전부 제외
    ]
    result = review._select_originals(rounds)
    assert result == [(1, 700), (2, 701)]                     # 느린 정답 2개가 실제로 선정됨
    assert not ({3, 4, 5, 6} & {qid for qid, _ in result})    # 빠른 정답은 하나도 안 뽑힘


# ---------- recommend_review: 글루(원문제 선정 + 유사문제 부착) ----------

def test_recommend_review_coldstart_returns_empty(monkeypatch):
    """이력 없으면 빈 복습 세트 — 유사문제 조회조차 하지 않는다."""
    monkeypatch.setattr(review.db, "get_answer_rounds", lambda sid: [])
    assert review.recommend_review(42) == []


def test_recommend_review_attaches_similars_without_original(monkeypatch):
    """선정된 원문제마다 get_similar_problems 결과에서 [0](원문제)을 뗀 유사만 붙는다."""
    rounds = [_round(500, [(11, False), (12, True), (13, True)], {11: 1, 12: 1, 13: 1})]
    monkeypatch.setattr(review.db, "get_answer_rounds", lambda sid: rounds)
    monkeypatch.setattr(
        review.recommender, "get_similar_problems",
        lambda sid, pid, k: [pid, 9001, 9002],   # [원문제, 유사1, 유사2]
    )
    result = review.recommend_review(7, k=2)
    assert result == [{"problem_id": 11, "section_id": 500, "similar": [9001, 9002]}]


# ---------- /quiz/review 엔드포인트 배선 ----------

def test_review_endpoint_wraps_recommend_review(monkeypatch):
    """GET /quiz/review 핸들러가 recommend_review 결과를 {"reviews": ...}로 감싼다."""
    import app as app_module
    payload = [{"problem_id": 1, "section_id": 2, "similar": [3, 4]}]
    monkeypatch.setattr(app_module, "recommend_review", lambda sid, k: payload)
    assert app_module.review(student_id=7, k=2) == {"reviews": payload}
