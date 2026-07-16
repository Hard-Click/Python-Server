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
sys.modules["db"] = types.ModuleType("db")
_fake_vs = types.ModuleType("vector_store")
sys.modules["vector_store"] = _fake_vs

import recommender  # noqa: E402  (가짜 주입 후 import 해야 함)


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
