"""Qdrant 연결·동작 확인 (OpenAI 불필요, 돈 0).

가짜(랜덤) 벡터로 collection 생성 → upsert → search 가 되는지만 검증한다.
Qdrant URL/키가 올바른지, 컬렉션·검색이 동작하는지 확인용.
실제 임베딩(문장→벡터)은 OpenAI 키를 받은 뒤 smoke_test.py 로 한다.

실행:
    .env 에 QDRANT_URL / QDRANT_API_KEY 만 채우면 됨 (OPENAI 없어도 OK)
    .venv\\Scripts\\python.exe qdrant_check.py
"""
import random

import config
import vector_store   # 주의: embedding(OpenAI)은 import 안 함 → 키 없어도 실행됨


def _fake_vec(seed: int) -> list[float]:
    rnd = random.Random(seed)
    return [rnd.uniform(-1, 1) for _ in range(config.EMBEDDING_DIM)]


def main() -> None:
    vector_store.ensure_collection()
    print("OK: 컬렉션 준비 완료 (Qdrant 연결 성공)")

    rows = [
        {"question_id": 1001, "course_id": 1, "section_id": 1, "difficulty": 2},
        {"question_id": 1002, "course_id": 1, "section_id": 1, "difficulty": 2},
        {"question_id": 1003, "course_id": 1, "section_id": 2, "difficulty": 1},
    ]
    vectors = [_fake_vec(r["question_id"]) for r in rows]
    vector_store.upsert(rows, vectors, ["check"] * len(rows))
    print(f"OK: 더미 {len(rows)}개 저장(upsert) 성공")

    hits = vector_store.search(1001, {"courseId": 1}, {1001}, limit=2)
    print(f"OK: 검색 성공 (course=1, 1001 제외) → {hits}")

    vector_store.delete_ids([r["question_id"] for r in rows])
    print("정리: 테스트용 더미 삭제 완료")

    print("\n[성공] Qdrant URL/키/컬렉션/저장/검색/필터 전부 정상.")
    print("      (랜덤 벡터라 '유사도'는 의미 없음 — 진짜 유사도는 OpenAI 키 받은 뒤 smoke_test.py)")


if __name__ == "__main__":
    main()
