"""배치 인덱서: 공유 RDS에서 퀴즈 문제를 직접 읽어 임베딩 → Qdrant 동기화.

- 새/수정된 문제만 임베딩 (content_hash 비교로 불필요한 재임베딩·비용 방지)
- RDS에서 삭제된 문제는 Qdrant에서도 제거 (동기화)

실행:  python indexer.py     (크론으로 주기 실행 권장, 예: 10분마다 or 야간)
"""
import hashlib
import json

import db
import embedding
import vector_store


def _content_hash(text: str, explanation: str | None, difficulty) -> str:
    # difficulty도 포함 → 난이도만 바뀌어도 payload가 갱신됨
    raw = (text or "") + "\x00" + (explanation or "") + "\x00" + str(difficulty)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _build_text(row: dict) -> str:
    """임베딩 입력: 문제 본문 + (있으면) 해설."""
    text = row["question_text"]
    if row.get("explanation"):
        text += "\n" + row["explanation"]
    return text


def _fetch_all_questions() -> list[dict]:
    # difficulty는 quiz_question에 컬럼 추가(마이그레이션) 후 채워진다.
    sql = """
        SELECT q.question_id, q.question_text, q.explanation, q.difficulty,
               qz.course_id, qz.section_id
        FROM quiz_question q
        JOIN quiz qz ON q.quiz_id = qz.quiz_id
    """
    conn = db.get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
            return cur.fetchall()
    finally:
        conn.close()


def reindex() -> dict:
    vector_store.ensure_collection()

    rows = _fetch_all_questions()
    desired = {r["question_id"]: r for r in rows}
    existing = vector_store.existing_id_hashes()

    # 새/수정된 문제만 골라 임베딩
    changed_rows: list[dict] = []
    changed_hashes: list[str] = []
    for qid, row in desired.items():
        h = _content_hash(row["question_text"], row["explanation"], row.get("difficulty"))
        if existing.get(qid) != h:
            changed_rows.append(row)
            changed_hashes.append(h)

    # RDS에 없는데 Qdrant에 남아있는 것 = 삭제된 문제
    to_delete = [qid for qid in existing if qid not in desired]

    if changed_rows:
        vectors = embedding.embed([_build_text(r) for r in changed_rows])
        vector_store.upsert(changed_rows, vectors, changed_hashes)
    if to_delete:
        vector_store.delete_ids(to_delete)

    return {"total": len(desired), "embedded": len(changed_rows), "deleted": len(to_delete)}


if __name__ == "__main__":
    print(json.dumps(reindex(), ensure_ascii=False))
