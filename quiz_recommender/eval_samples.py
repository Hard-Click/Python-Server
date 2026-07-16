"""Day 12-14 검증: 각 문제의 top-k 유사문제가 같은 type인지 확인.

선행: seed_samples.py 로 적재 완료(임베딩 키 필요).
이 스크립트 자체는 Qdrant 검색만 하므로 임베딩 키 없이도 돌아간다(적재만 돼 있으면).

실행:  .venv\\Scripts\\python.exe eval_samples.py
"""
import json
from pathlib import Path

import vector_store


def main() -> None:
    problems = json.loads(
        (Path(__file__).parent / "sample_problems.json").read_text(encoding="utf-8")
    )["problems"]
    by_id = {p["question_id"]: p for p in problems}

    hit = 0
    for p in problems:
        sim = vector_store.search(
            p["question_id"], {"courseId": p["course_id"]}, {p["question_id"]}, limit=2
        )
        pairs = [(s, by_id[s]["type"]) for s in sim if s in by_id]
        top1_same = bool(pairs) and pairs[0][1] == p["type"]
        hit += 1 if top1_same else 0
        mark = "OK" if top1_same else "XX"
        print(f"[{mark}] {p['question_id']} ({p['type']}) -> {pairs}")

    total = len(problems)
    print(f"\ntop-1 동일유형 정확도: {hit}/{total} = {hit / total:.0%}")
    print("(같은 type이 top-1로 나올수록 추천 품질이 좋다는 뜻)")


if __name__ == "__main__":
    main()
