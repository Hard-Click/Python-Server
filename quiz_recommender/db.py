"""공유 RDS(MySQL) 연결. 백엔드와 같은 DB를 읽기 전용으로 사용한다.
비밀번호는 환경변수로만 받는다 (Python-Server의 db.py와 동일 방식)."""
import os
import pymysql
from pymysql.cursors import DictCursor


def get_connection():
    return pymysql.connect(
        host=os.environ["DB_HOST"],
        port=int(os.environ.get("DB_PORT", 3306)),
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
        database=os.environ["DB_NAME"],
        cursorclass=DictCursor,
        autocommit=True,
    )


def get_answer_rounds(student_id: int) -> list[dict]:
    """학생의 제출 이력을 시간순 '라운드'(제출 1건 = 퀴즈 1회)로 묶어 반환.
    각 라운드: {"section_id": int, "answers": [(question_id, is_correct), ...]}

    quiz_submission_answer 에는 member_id 가 없다. 학생은 부모 quiz_submission 에
    있으므로 submission_id 로 조인한다. section 은 quiz_question 이 아니라
    quiz 테이블에 있으므로 quiz 까지 조인해 유도한다.
    난이도 사다리(직전 라운드 결과로 승급/강등) 판정에 쓴다.
    """
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT qs.submission_id, q.section_id,
                       qsa.question_id, qsa.is_correct
                FROM quiz_submission_answer qsa
                JOIN quiz_submission qs ON qs.submission_id = qsa.submission_id
                JOIN quiz_question qq   ON qq.question_id   = qsa.question_id
                JOIN quiz q             ON q.quiz_id        = qq.quiz_id
                WHERE qs.member_id = %s
                ORDER BY qs.submitted_at, qs.submission_id
                """,
                (student_id,),
            )
            rows = cur.fetchall()
    finally:
        conn.close()

    rounds: list[dict] = []
    by_submission: dict[int, dict] = {}
    for r in rows:
        rd = by_submission.get(r["submission_id"])
        if rd is None:
            rd = {"section_id": r["section_id"], "answers": []}
            by_submission[r["submission_id"]] = rd
            rounds.append(rd)
        rd["answers"].append((r["question_id"], bool(r["is_correct"])))
    return rounds
