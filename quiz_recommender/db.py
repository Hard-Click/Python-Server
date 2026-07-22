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


# 정규 퀴즈 제출 이력. section 은 quiz_question 이 아니라 quiz 테이블에 있으므로 조인해 유도.
# quiz_submission_answer 에는 member_id 가 없어 부모 quiz_submission 을 submission_id 로 조인한다.
_REGULAR_SQL = """
    SELECT qs.submission_id AS sid, qs.submitted_at AS ts, q.section_id AS section_id,
           qsa.question_id AS question_id, qsa.is_correct AS is_correct{time_col}
    FROM quiz_submission_answer qsa
    JOIN quiz_submission qs ON qs.submission_id = qsa.submission_id
    JOIN quiz_question qq   ON qq.question_id   = qsa.question_id
    JOIN quiz q             ON q.quiz_id        = qq.quiz_id
    WHERE qs.member_id = %s
"""

# 유사퀴즈(복습) 제출 이력. 재응시 허용이라 같은 학생의 여러 제출이 시간순으로 쌓인다(V3.5.12).
_SIMILAR_SQL = """
    SELECT sqs.submission_id AS sid, sqs.submitted_at AS ts, q.section_id AS section_id,
           sqsa.question_id AS question_id, sqsa.is_correct AS is_correct,
           sqsa.time_spent_seconds AS time_spent_seconds
    FROM similar_quiz_submission_answer sqsa
    JOIN similar_quiz_submission sqs ON sqs.submission_id = sqsa.submission_id
    JOIN quiz_question qq            ON qq.question_id     = sqsa.question_id
    JOIN quiz q                      ON q.quiz_id          = qq.quiz_id
    WHERE sqs.member_id = %s
"""


def get_answer_rounds(student_id: int) -> list[dict]:
    """학생의 제출 이력을 시간순 '라운드'(제출 1건 = 퀴즈 1회)로 묶어 반환.
    각 라운드: {"section_id": int,
               "answers": [(question_id, is_correct), ...],
               "times":   {question_id: 풀이초 | None}}

    정규 퀴즈 + 유사퀴즈(복습) 제출을 모두 포함해 submitted_at 순으로 정렬한다 —
    복습을 푼 결과가 다음 복습의 사다리·시간 신호에 반영되는 루프를 위해서다.
    answers 튜플 모양은 (qid, ok) 그대로 유지한다(eval/사다리가 2-튜플 언패킹에 의존) —
    시간(신호③)은 병렬 dict(times)로만 얹는다.

    폴백: time_spent_seconds 컬럼이 없는 환경(1054)에선 시간 없이, 유사퀴즈 제출 테이블이
    아직 없는 환경(1146)에선 정규 퀴즈만으로 동작한다.
    """
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            # 정규: time_spent_seconds 컬럼 유무 폴백
            try:
                cur.execute(_REGULAR_SQL.format(time_col=", qsa.time_spent_seconds"), (student_id,))
            except (pymysql.err.OperationalError, pymysql.err.ProgrammingError) as e:
                if e.args and e.args[0] == 1054:  # Unknown column → 마이그레이션 전 환경
                    cur.execute(_REGULAR_SQL.format(time_col=""), (student_id,))
                else:
                    raise
            rows = [("r", r) for r in cur.fetchall()]

            # 유사퀴즈: 제출 테이블이 없는 환경(1146)이면 건너뜀
            try:
                cur.execute(_SIMILAR_SQL, (student_id,))
                rows += [("s", r) for r in cur.fetchall()]
            except (pymysql.err.OperationalError, pymysql.err.ProgrammingError) as e:
                if not (e.args and e.args[0] == 1146):  # Table doesn't exist 외엔 재전파
                    raise
    finally:
        conn.close()

    # (소스, submission_id)로 라운드를 모으고 submitted_at 순으로 정렬.
    # 두 테이블의 submission_id 공간이 겹칠 수 있어 소스 태그로 키를 구분한다.
    by_key: dict[tuple, dict] = {}
    for source, r in rows:
        key = (source, r["sid"])
        rd = by_key.get(key)
        if rd is None:
            rd = {"section_id": r["section_id"], "answers": [], "times": {}, "_ts": r["ts"]}
            by_key[key] = rd
        rd["answers"].append((r["question_id"], bool(r["is_correct"])))
        rd["times"][r["question_id"]] = r.get("time_spent_seconds")

    ordered = sorted(by_key.values(), key=lambda rd: (rd["_ts"] is None, rd["_ts"]))
    for rd in ordered:
        rd.pop("_ts", None)
    return ordered
