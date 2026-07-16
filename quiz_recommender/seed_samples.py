"""sample_problems.json 을 임베딩해 Qdrant에 적재 (Day 5 시딩).

⚠️ 임베딩 키(다음 주 Google AI Studio 키)가 .env에 있어야 실행된다.
   embedding.py 를 Google 임베딩으로 교체한 뒤 실행할 것.

실행:  .venv\\Scripts\\python.exe seed_samples.py
"""
import json
from pathlib import Path

import embedding
import vector_store


def main() -> None:
    data = json.loads((Path(__file__).parent / "sample_problems.json").read_text(encoding="utf-8"))
    problems = data["problems"]

    vector_store.ensure_collection()
    rows = [
        {"question_id": p["question_id"], "course_id": p["course_id"],
         "section_id": p["section_id"], "difficulty": p["difficulty"]}
        for p in problems
    ]
    vectors = embedding.embed([p["question_text"] for p in problems])
    vector_store.upsert(rows, vectors, ["sample"] * len(rows))
    print(f"OK: 샘플 {len(rows)}개 임베딩·적재 완료")


if __name__ == "__main__":
    main()
