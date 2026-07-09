"""Qdrant 벡터 저장/검색. questionId를 point id로 사용한다."""
from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance, VectorParams, PointStruct,
    Filter, FieldCondition, MatchValue, Range, HasIdCondition,
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
            payload={
                "courseId": r["course_id"],
                "sectionId": r["section_id"],
                "difficulty": r.get("difficulty"),   # 스키마에 difficulty 추가 후 채워짐
                "hash": h,
            },
        )
        for r, vec, h in zip(rows, vectors, hashes)
    ]
    _client.upsert(collection_name=config.COLLECTION, points=points)


def delete_ids(ids: list[int]) -> None:
    _client.delete(collection_name=config.COLLECTION, points_selector=list(ids))


def retrieve_meta(problem_id: int) -> dict | None:
    """저장된 문제의 course/section/difficulty 조회. 없으면 None(=미인덱싱)."""
    res = _client.retrieve(
        collection_name=config.COLLECTION,
        ids=[problem_id], with_payload=True, with_vectors=False,
    )
    if not res:
        return None
    p = res[0].payload or {}
    return {"courseId": p.get("courseId"), "sectionId": p.get("sectionId"), "difficulty": p.get("difficulty")}


def search(query_id: int, spec: dict, exclude_ids: set[int], limit: int) -> list[int]:
    """query_id 문제 벡터를 기준으로 spec 필터에 맞는 유사 문제 검색.
    spec 예: {"courseId":5,"sectionId":12,"difficulty":2}
             {"courseId":5,"sectionId":12,"difficulty_range":(1,3)}
             {"courseId":5}
    저장된 벡터를 query로 재사용하므로 텍스트 재전송이 필요 없다."""
    must = [FieldCondition(key="courseId", match=MatchValue(value=spec["courseId"]))]
    if "sectionId" in spec:
        must.append(FieldCondition(key="sectionId", match=MatchValue(value=spec["sectionId"])))
    if "difficulty" in spec:
        must.append(FieldCondition(key="difficulty", match=MatchValue(value=spec["difficulty"])))
    if "difficulty_range" in spec:
        lo, hi = spec["difficulty_range"]
        must.append(FieldCondition(key="difficulty", range=Range(gte=lo, lte=hi)))

    res = _client.query_points(
        collection_name=config.COLLECTION,
        query=query_id,
        query_filter=Filter(
            must=must,
            must_not=[HasIdCondition(has_id=list(exclude_ids))] if exclude_ids else None,
        ),
        limit=limit, with_payload=False,
    )
    return [int(p.id) for p in res.points]
