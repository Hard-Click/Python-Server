"""Qdrant 벡터 저장/검색. questionId를 point id로 사용한다."""
from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance, VectorParams, PointStruct,
    Filter, FieldCondition, MatchValue, HasIdCondition,
)
import config

_client = QdrantClient(url=config.QDRANT_URL, api_key=config.QDRANT_API_KEY)


def ensure_collection() -> None:
    """컬렉션이 없으면 코사인 거리로 생성 (최초 1회)."""
    if not _client.collection_exists(config.COLLECTION):
        _client.create_collection(
            collection_name=config.COLLECTION,
            vectors_config=VectorParams(size=config.EMBEDDING_DIM, distance=Distance.COSINE),
        )


def existing_id_hashes() -> dict[int, str]:
    """이미 저장된 {questionId: content_hash} 전체 조회.
    배치 인덱서가 '바뀐 문제만' 다시 임베딩할지 판단하는 데 쓴다."""
    result: dict[int, str] = {}
    offset = None
    while True:
        points, offset = _client.scroll(
            collection_name=config.COLLECTION,
            with_payload=["hash"], with_vectors=False,
            limit=1000, offset=offset,
        )
        for p in points:
            result[int(p.id)] = (p.payload or {}).get("hash")
        if offset is None:
            break
    return result


def upsert(rows: list[dict], vectors: list[list[float]], hashes: list[str]) -> None:
    points = [
        PointStruct(
            id=r["question_id"],
            vector=vec,
            payload={"courseId": r["course_id"], "sectionId": r["section_id"], "hash": h},
        )
        for r, vec, h in zip(rows, vectors, hashes)
    ]
    _client.upsert(collection_name=config.COLLECTION, points=points)


def delete_ids(ids: list[int]) -> None:
    _client.delete(collection_name=config.COLLECTION, points_selector=list(ids))


def search_similar(question_id: int, course_id: int, exclude_ids: set[int], limit: int) -> list[int]:
    """question_id 문제와 유사한 문제를 같은 course 안에서 검색.
    저장된 벡터를 query로 재사용하므로 텍스트 재전송이 필요 없다."""
    res = _client.query_points(
        collection_name=config.COLLECTION,
        query=question_id,   # 저장된 point를 기준으로 최근접 검색
        query_filter=Filter(
            must=[FieldCondition(key="courseId", match=MatchValue(value=course_id))],
            must_not=[HasIdCondition(has_id=list(exclude_ids))],
        ),
        limit=limit, with_payload=False,
    )
    return [int(p.id) for p in res.points]
