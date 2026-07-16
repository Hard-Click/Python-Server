"""Day 4 첫 성공 스모크 테스트 (RDS 불필요).

샘플 문제 4개를 임베딩 → Qdrant 저장 → 검색 1회.
"비슷한 문제"가 결과로 나오면 성공.

실행 (PowerShell):
    $env:GEMINI_API_KEY="..."
    $env:QDRANT_URL="https://xxxx.aws.cloud.qdrant.io:6333"
    $env:QDRANT_API_KEY="..."
    python smoke_test.py
"""
import embedding
import vector_store

# 같은 course(1) 안에서, 유사 쌍을 일부러 섞음:
#   1↔2 = 이차방정식 인수분해,  3↔4 = 분모 유리화
SAMPLES = [
    {"question_id": 1, "course_id": 1, "section_id": 1, "difficulty": 2,
     "text": "x^2 - 5x + 6 = 0 의 두 근을 구하시오."},
    {"question_id": 2, "course_id": 1, "section_id": 1, "difficulty": 2,
     "text": "x^2 - 7x + 12 = 0 을 인수분해하여 근을 구하시오."},
    {"question_id": 3, "course_id": 1, "section_id": 2, "difficulty": 1,
     "text": "1 / (root2 - 1) 의 분모를 유리화하시오."},
    {"question_id": 4, "course_id": 1, "section_id": 2, "difficulty": 1,
     "text": "3 / (root5 + root2) 의 분모를 유리화하시오."},
]


def _text(qid: int) -> str:
    return next(s["text"] for s in SAMPLES if s["question_id"] == qid)


def main() -> None:
    vector_store.ensure_collection()

    rows = [
        {"question_id": s["question_id"], "course_id": s["course_id"],
         "section_id": s["section_id"], "difficulty": s["difficulty"]}
        for s in SAMPLES
    ]
    vectors = embedding.embed([s["text"] for s in SAMPLES])
    hashes = ["smoke"] * len(SAMPLES)          # 스모크용 더미 해시
    vector_store.upsert(rows, vectors, hashes)
    print(f"OK: {len(SAMPLES)}개 임베딩·저장 완료")

    query_id = 1
    similar = vector_store.search(query_id, {"courseId": 1}, {query_id}, limit=2)

    print(f"\n[문제 {query_id}] {_text(query_id)}")
    print("→ 비슷하다고 찾은 문제:")
    for sid in similar:
        print(f"   #{sid}: {_text(sid)}")

    if similar and similar[0] == 2:
        print("\n[성공] 같은 유형(이차방정식 인수분해, #2)을 가장 비슷하게 찾음.")
    else:
        print("\n[확인 필요] 기대: #2가 상위. 결과가 다르면 임베딩 텍스트/모델 점검.")


if __name__ == "__main__":
    main()
