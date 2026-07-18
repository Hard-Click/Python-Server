"""get_similar_problems 계약 단위테스트 (mock).

- recommender는 embedding을 import하지 않으므로 Gemini 키 없이 실행된다.
- db / vector_store 는 config(env 필수)·qdrant_client를 끌어오므로, import 전에 가짜 모듈로
  대체한다. 그래서 RDS·Qdrant·키 전혀 없이 '반환 규칙(계약)'만 순수 검증한다.
- 종준 FSRS와 확정한 계약을 고정 스펙으로 못박아, 나중에 통합 시 우리 쪽 보장 근거가 된다.
"""
import sys
import types

import pytest

# --- recommender import 전에 무거운 의존성을 가짜 모듈로 주입 ---
_fake_db = types.ModuleType("db")
_fake_db.get_answer_rounds = lambda sid: []   # 기본: 이력 없음(콜드스타트) → 개인화는 베이스라인에 양보
sys.modules["db"] = _fake_db
_fake_vs = types.ModuleType("vector_store")
sys.modules["vector_store"] = _fake_vs

import recommender  # noqa: E402  (가짜 주입 후 import 해야 함)
import personalize  # noqa: E402


def _seq_search(sequence):
    """호출마다 sequence의 다음 리스트를 반환하는 가짜 vector_store.search.
    실제 코드처럼 exclude(원문제·기존 발견) 제외 + limit 상한을 반영한다."""
    calls = iter(sequence)

    def _search(query_id, spec, exclude_ids, limit):
        try:
            candidates = next(calls)
        except StopIteration:
            candidates = []
        return [c for c in candidates if c not in exclude_ids][:limit]

    return _search


@pytest.fixture
def rec(monkeypatch):
    """기본 상황: 문제 존재 O, 인덱싱 O, 유사 2개(201,202). 각 테스트가 필요분만 덮어씀."""
    monkeypatch.setattr(recommender, "_exists_in_rds", lambda pid: True)
    monkeypatch.setattr(
        recommender.vector_store, "retrieve_meta",
        lambda pid: {"courseId": 1, "sectionId": 2, "difficulty": 2},
        raising=False,
    )
    monkeypatch.setattr(
        recommender.vector_store, "search", _seq_search([[201, 202]]), raising=False
    )
    return recommender


def test_invalid_id_returns_empty(rec, monkeypatch):
    """잘못된 id → 빈 리스트(에러 규칙 A)."""
    monkeypatch.setattr(rec, "_exists_in_rds", lambda pid: False)
    assert rec.get_similar_problems(1, 999, k=2) == []


def test_valid_but_not_indexed_returns_only_self(rec, monkeypatch):
    """유효하지만 아직 인덱싱 전 → 원문제만(잘못된 id로 오판 안 함)."""
    monkeypatch.setattr(rec.vector_store, "retrieve_meta", lambda pid: None, raising=False)
    assert rec.get_similar_problems(1, 100, k=2) == [100]


def test_normal_two_similars(rec):
    """정상 + 유사 충분 → [원문제, 유사1, 유사2]."""
    assert rec.get_similar_problems(1, 100, k=2) == [100, 201, 202]


def test_only_one_similar(rec, monkeypatch):
    """유사 1개뿐 → [원문제, 유사1]."""
    monkeypatch.setattr(rec.vector_store, "search", _seq_search([[201]]), raising=False)
    assert rec.get_similar_problems(1, 100, k=2) == [100, 201]


def test_no_similar_returns_only_self(rec, monkeypatch):
    """유사 없음 → [원문제] (원문제 있음 = 정상)."""
    monkeypatch.setattr(rec.vector_store, "search", _seq_search([[]]), raising=False)
    assert rec.get_similar_problems(1, 100, k=2) == [100]


def test_order_and_original_excluded(rec, monkeypatch):
    """순서 보장(data[0]=원문제) + 원문제 자신은 유사 후보에서 제외."""
    monkeypatch.setattr(
        rec.vector_store, "search", _seq_search([[100, 201, 202]]), raising=False
    )
    result = rec.get_similar_problems(1, 100, k=2)
    assert result[0] == 100
    assert 100 not in result[1:]
    assert result == [100, 201, 202]


def test_fallback_accumulates_across_specs(rec, monkeypatch):
    """폴백: 1st spec 1개 + 2nd spec 1개 → 누적해서 k=2 채움."""
    monkeypatch.setattr(
        rec.vector_store, "search", _seq_search([[201], [202]]), raising=False
    )
    assert rec.get_similar_problems(1, 100, k=2) == [100, 201, 202]


def test_no_duplicate_across_fallback(rec, monkeypatch):
    """앞 spec에서 나온 후보가 다음 spec에서 또 나와도 중복 없음."""
    monkeypatch.setattr(
        rec.vector_store, "search", _seq_search([[201], [201, 202]]), raising=False
    )
    assert rec.get_similar_problems(1, 100, k=2) == [100, 201, 202]


def test_respects_k(rec, monkeypatch):
    """유사 후보가 넘쳐도 정확히 k개만."""
    monkeypatch.setattr(
        rec.vector_store, "search", _seq_search([[201, 202, 203, 204]]), raising=False
    )
    assert rec.get_similar_problems(1, 100, k=2) == [100, 201, 202]


def test_respects_k_three(rec, monkeypatch):
    """k는 파라미터대로(2가 아닌 3도 동작)."""
    monkeypatch.setattr(
        rec.vector_store, "search", _seq_search([[201, 202, 203, 204]]), raising=False
    )
    assert rec.get_similar_problems(1, 100, k=3) == [100, 201, 202, 203]


# --- 장애 시 degradation (종준 확정 정책 ⓐ): 예외 대신 [원문제]로 눌러 추천만 스킵 ---

def test_rds_failure_degrades_to_self(rec, monkeypatch):
    """RDS 존재확인 중 장애 → 예외 전파 없이 [원문제]만 (배치가 skip 처리)."""
    def _boom(pid):
        raise RuntimeError("RDS down")
    monkeypatch.setattr(rec, "_exists_in_rds", _boom)
    assert rec.get_similar_problems(1, 100, k=2) == [100]


def test_qdrant_meta_failure_degrades_to_self(rec, monkeypatch):
    """Qdrant retrieve_meta 장애 → [원문제]만 (id는 유효하므로 [] 아님)."""
    def _boom(pid):
        raise RuntimeError("Qdrant down")
    monkeypatch.setattr(rec.vector_store, "retrieve_meta", _boom, raising=False)
    assert rec.get_similar_problems(1, 100, k=2) == [100]


def test_qdrant_search_failure_degrades_to_self(rec, monkeypatch):
    """유사문제 검색(search) 장애 → [원문제]만 (복습은 그대로 진행)."""
    def _boom(query_id, spec, exclude_ids, limit):
        raise RuntimeError("Qdrant search failed")
    monkeypatch.setattr(rec.vector_store, "search", _boom, raising=False)
    assert rec.get_similar_problems(1, 100, k=2) == [100]


# --- 강사 격리 (instructorId) ---

def test_instructor_filter_in_every_fallback_spec(rec, monkeypatch):
    """meta에 instructorId가 있으면 모든 폴백 spec에 강사 격리 필터가 걸린다."""
    monkeypatch.setattr(
        rec.vector_store, "retrieve_meta",
        lambda pid: {"courseId": 1, "sectionId": 2, "difficulty": 2, "instructorId": 9221},
        raising=False,
    )
    seen_specs = []
    def _spy(query_id, spec, exclude_ids, limit):
        seen_specs.append(dict(spec))
        return []
    monkeypatch.setattr(rec.vector_store, "search", _spy, raising=False)
    rec.get_similar_problems(1, 100, k=2)
    assert seen_specs, "search가 최소 1회 호출돼야 함"
    assert all(s.get("instructorId") == 9221 for s in seen_specs)


def test_no_instructor_in_meta_keeps_specs_clean(rec, monkeypatch):
    """옛 인덱스(instructorId payload 없음) → spec에 필터 미포함(하위호환)."""
    seen_specs = []
    def _spy(query_id, spec, exclude_ids, limit):
        seen_specs.append(dict(spec))
        return []
    monkeypatch.setattr(rec.vector_store, "search", _spy, raising=False)
    rec.get_similar_problems(1, 100, k=2)
    assert all("instructorId" not in s for s in seen_specs)


# --- 개인화 연결 ---

def test_personalized_results_come_first(rec, monkeypatch):
    """개인화가 결과를 내면 그것이 유사 슬롯을 우선 차지한다."""
    monkeypatch.setattr(
        rec.personalize, "personalized_recommend",
        lambda sid, qid, cid, k: [301, 302],
    )
    assert rec.get_similar_problems(7, 100, k=2) == [100, 301, 302]


def test_personalized_partial_filled_by_baseline(rec, monkeypatch):
    """개인화가 1개만 내면 나머지는 베이스라인 유사도로 채우되 중복 없음."""
    monkeypatch.setattr(
        rec.personalize, "personalized_recommend",
        lambda sid, qid, cid, k: [201],   # 201은 베이스라인 첫 후보와 동일 → 중복 제외 확인
    )
    assert rec.get_similar_problems(7, 100, k=2) == [100, 201, 202]


def test_personalize_failure_falls_back_to_baseline(rec, monkeypatch):
    """개인화 내부 장애(RDS 등) → 베이스라인 추천은 그대로 살아있다."""
    def _boom(sid, qid, cid, k):
        raise RuntimeError("RDS down in personalize")
    monkeypatch.setattr(rec.personalize, "personalized_recommend", _boom)
    assert rec.get_similar_problems(7, 100, k=2) == [100, 201, 202]


def test_cold_start_uses_baseline(rec):
    """이력 없음(콜드스타트, 기본 fixture) → personalize가 []를 반환하고 베이스라인이 채움."""
    assert rec.get_similar_problems(7, 100, k=2) == [100, 201, 202]


def test_personalize_cold_start_returns_empty():
    """personalize 자체 계약: 이력 없으면 [] (베이스라인에 양보)."""
    assert personalize.personalized_recommend(99, 100, 1, 2) == []
