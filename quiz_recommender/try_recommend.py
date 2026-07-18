"""유사문제 추천을 눈으로 확인하는 데모 (샘플 대상, RDS·Gemini 불필요).

seed_samples.py로 적재된 샘플을 대상으로, 문제 id 하나를 주면
원문제 + 유사문제 2개를 '실제 텍스트'로 보여준다.

- 추천 핵심(vector_store.search)은 get_similar_problems와 동일.
  단, RDS 존재확인(_exists_in_rds)은 건너뜀 — 샘플은 RDS에 없으니까.

사용:
    .venv\\Scripts\\python.exe try_recommend.py 101
    (id 안 주면 101)
"""
import sys
import json
from pathlib import Path

import vector_store


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")  # Windows 콘솔 인코딩 오류 방지
    pid = int(sys.argv[1]) if len(sys.argv) > 1 else 101

    problems = json.loads(
        (Path(__file__).parent / "sample_problems.json").read_text(encoding="utf-8")
    )["problems"]
    by_id = {p["question_id"]: p for p in problems}

    if pid not in by_id:
        print(f"#{pid}는 샘플에 없어. 사용 가능 id: {sorted(by_id)}")
        return

    course = by_id[pid]["course_id"]
    similar = vector_store.search(pid, {"courseId": course}, {pid}, limit=2)

    print(f"[원문제 #{pid}] ({by_id[pid]['type']})")
    print(f"   {by_id[pid]['question_text']}")
    print("\n→ 추천된 유사문제:")
    for i, sid in enumerate(similar, 1):
        s = by_id.get(sid, {})
        same = "✓같은유형" if s.get("type") == by_id[pid]["type"] else "✗다른유형"
        print(f"   유사{i} #{sid} ({s.get('type', '?')}) {same}")
        print(f"      {s.get('question_text', '?')}")


if __name__ == "__main__":
    main()
