"""RDS 연결·읽기 검증 (읽기 전용, Google 임베딩 키 불필요).

.env 에 DB_HOST / DB_PORT / DB_USER / DB_PASSWORD / DB_NAME 이 있어야 한다.
SELECT / SHOW 만 실행한다 — 데이터를 절대 변경하지 않는다.

실행:  .venv\\Scripts\\python.exe rds_check.py
"""
import config  # noqa: F401  (import 시 .env 로드 — DB_* 포함)
import db


def main() -> None:
    import sys
    sys.stdout.reconfigure(encoding="utf-8")  # Windows 콘솔(cp949) 인코딩 오류 방지
    conn = db.get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS n FROM quiz")
            print("quiz 행 수:", cur.fetchone()["n"])
            cur.execute("SELECT COUNT(*) AS n FROM quiz_question")
            print("quiz_question 행 수:", cur.fetchone()["n"])

            cur.execute("SHOW COLUMNS FROM quiz_question LIKE 'difficulty'")
            has_diff = cur.fetchone() is not None
            print("difficulty 컬럼 존재:", has_diff, "(마이그레이션 V3.5.1 적용 여부)")

            cols = "q.question_id, q.question_text, qz.course_id, qz.section_id"
            if has_diff:
                cols += ", q.difficulty"
            cur.execute(
                f"SELECT {cols} FROM quiz_question q "
                f"JOIN quiz qz ON q.quiz_id = qz.quiz_id LIMIT 3"
            )
            rows = cur.fetchall()
            print(f"\n인덱서 SELECT 실제 실행 성공 - 샘플 {len(rows)}개:")
            for r in rows:
                text = (r["question_text"] or "")[:30]
                print(f"  #{r['question_id']} course={r['course_id']} section={r['section_id']} : {text}...")

        print("\n[성공] RDS 연결 + 읽기 정상 (Day7 SELECT 실증).")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
