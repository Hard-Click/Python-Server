"""평가 라벨 초안 생성기 — eval_labels.json 수작업을 '몇 개 골라 넣기'로 줄인다.

DB(RDS)에서 실데이터를 읽어 라벨 초안을 자동으로 채운다:
  · students  : 오답/정답 question_id, 오답에서 유도한 weak_sections  → 전부 자동
  · scenarios : (학생 × 그 학생이 틀린 문제) 조합                     → 전부 자동
  · queries   : course_id 자동 + relevant_ids 는 빈 배열로 두고,
                사람이 고르라고 같은 section 후보 목록(_candidates)을 문제 지문
                미리보기와 함께 붙여준다.  ← 여기만 수작업

사용법 (.env 가 있는 본인 환경에서):
    .venv\\Scripts\\python.exe build_eval_labels_draft.py                # 오답 있는 학생 자동 탐색
    .venv\\Scripts\\python.exe build_eval_labels_draft.py 9001 9002     # 데모 학생 member_id 지정

출력:  eval_labels.draft.json
이후:  _candidates 를 보고 relevant_ids 를 채운 뒤(각 query 2~3개면 충분),
       "_" 로 시작하는 도우미 필드는 지우고 eval_labels.json 으로 저장하면 끝.
       (eval_metrics.py 는 relevant_ids/course_id 만 읽으므로 안 지워도 동작은 한다)
"""
import json
import sys
from pathlib import Path

try:
    from . import db
except ImportError:
    import db

K = 2                       # eval_metrics 의 top-k
MAX_STUDENTS = 5            # member_id 미지정 시 자동 탐색할 학생 수
MAX_QUERIES_PER_STUDENT = 6 # 학생당 시나리오(=원문제) 상한 — 총 20~30개 맞추기용
TEXT_PREVIEW = 60           # 후보 지문 미리보기 길이


def _fetch_students_with_wrong_answers(cur, limit: int) -> list[int]:
    """오답이 1개 이상 있는 학생을 오답 많은 순으로 찾는다."""
    cur.execute(
        """
        SELECT qs.member_id, COUNT(*) AS wrong_cnt
        FROM quiz_submission_answer qsa
        JOIN quiz_submission qs ON qs.submission_id = qsa.submission_id
        WHERE qsa.is_correct = 0
        GROUP BY qs.member_id
        ORDER BY wrong_cnt DESC
        LIMIT %s
        """,
        (limit,),
    )
    return [r["member_id"] for r in cur.fetchall()]


def _fetch_answer_rows(cur, member_ids: list[int]) -> list[dict]:
    """학생들의 정오답 + 그 문제의 section/course (quiz 조인으로 유도)."""
    placeholders = ",".join(["%s"] * len(member_ids))
    cur.execute(
        f"""
        SELECT qs.member_id, qsa.question_id, qsa.is_correct,
               q.section_id, q.course_id
        FROM quiz_submission_answer qsa
        JOIN quiz_submission qs ON qs.submission_id = qsa.submission_id
        JOIN quiz_question qq   ON qq.question_id   = qsa.question_id
        JOIN quiz q             ON q.quiz_id        = qq.quiz_id
        WHERE qs.member_id IN ({placeholders})
        """,
        member_ids,
    )
    return cur.fetchall()


def _fetch_section_candidates(cur, course_id: int, section_id: int, exclude_id: int) -> list[dict]:
    """원문제와 같은 course+section 의 다른 문제들 — relevant_ids 고르기용 후보."""
    cur.execute(
        """
        SELECT qq.question_id, qq.question_text
        FROM quiz_question qq
        JOIN quiz q ON q.quiz_id = qq.quiz_id
        WHERE q.course_id = %s AND q.section_id = %s AND qq.question_id <> %s
        ORDER BY qq.question_id
        """,
        (course_id, section_id, exclude_id),
    )
    return cur.fetchall()


def main() -> None:
    member_ids = [int(a) for a in sys.argv[1:]]

    conn = db.get_connection()
    try:
        with conn.cursor() as cur:
            if not member_ids:
                member_ids = _fetch_students_with_wrong_answers(cur, MAX_STUDENTS)
                if not member_ids:
                    raise SystemExit("오답 데이터가 있는 학생이 없습니다. 데모 오답을 먼저 심어주세요.")
                print(f"[자동 탐색] 오답 있는 학생: {member_ids}")

            rows = _fetch_answer_rows(cur, member_ids)
            if not rows:
                raise SystemExit(f"member_id {member_ids} 의 제출 데이터가 없습니다.")

            # ── students: 오답/정답/약점 전부 자동 ──
            students: dict[int, dict] = {}
            for r in rows:
                st = students.setdefault(r["member_id"], {
                    "wrong_question_ids": [], "weak_sections": [],
                    "solved_correct_ids": [], "note": "자동 생성(DB 실데이터)",
                })
                if r["is_correct"]:
                    st["solved_correct_ids"].append(r["question_id"])
                else:
                    st["wrong_question_ids"].append(r["question_id"])
                    if r["section_id"] not in st["weak_sections"]:
                        st["weak_sections"].append(r["section_id"])

            # ── queries + scenarios: 학생이 틀린 문제 = 복습 원문제 ──
            queries: dict[int, dict] = {}
            scenarios: list[dict] = []
            wrong_rows = [r for r in rows if not r["is_correct"]]
            per_student: dict[int, int] = {}
            for r in wrong_rows:
                if per_student.get(r["member_id"], 0) >= MAX_QUERIES_PER_STUDENT:
                    continue
                per_student[r["member_id"]] = per_student.get(r["member_id"], 0) + 1
                qid = r["question_id"]
                scenarios.append({"student_id": r["member_id"], "query_id": qid})
                if qid in queries:
                    continue
                cands = _fetch_section_candidates(cur, r["course_id"], r["section_id"], qid)
                queries[qid] = {
                    "course_id": r["course_id"],
                    "relevant_ids": [],  # ← 사람이 _candidates 에서 2~3개 골라 채운다
                    "note": f"section {r['section_id']} — _candidates 에서 같은 개념만 골라 relevant_ids 로",
                    "_section_id": r["section_id"],
                    "_candidates": [
                        {"question_id": c["question_id"],
                         "text": (c["question_text"] or "")[:TEXT_PREVIEW]}
                        for c in cands
                    ],
                }
    finally:
        conn.close()

    draft = {
        "_readme": [
            "자동 생성된 초안. students/scenarios 는 완성, queries.relevant_ids 만 채우면 된다.",
            "각 query 의 _candidates(같은 section 문제 + 지문 미리보기)에서 '진짜 같은 개념'인",
            "question_id 를 2~3개 골라 relevant_ids 에 넣는다. 다 채우면 '_' 필드는 지우고",
            "eval_labels.json 으로 저장 → eval_metrics.py 실행.",
        ],
        "k": K,
        "queries": queries,
        "students": students,
        "scenarios": scenarios,
    }

    out = Path(__file__).parent / "eval_labels.draft.json"
    out.write_text(json.dumps(draft, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n생성 완료: {out}")
    print(f"  students  {len(students)}명 (자동)")
    print(f"  scenarios {len(scenarios)}개 (자동)")
    print(f"  queries   {len(queries)}개 — relevant_ids 만 채우면 됩니다")


if __name__ == "__main__":
    main()
