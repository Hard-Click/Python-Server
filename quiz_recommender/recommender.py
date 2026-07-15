"""추천 진입 함수. 종준 FSRS가 같은 프로세스에서 직접 호출한다(HTTP 아님).

get_similar_problems(student_id, problem_id, k=2)  → 문제 id 리스트
  · 정상+유사 충분 → [원문제, 유사1, 유사2]
  · 유사 1개뿐     → [원문제, 유사1]
  · 유사 없음       → [원문제]        (원문제 있음 = 정상)
  · 잘못된 id       → []              (빈 리스트 = 에러, 규칙 A)
  data[0]=원문제, data[1:]=유사(유사도순)

폴백(유사문제가 k개 안 채워지면 단계적으로 완화):
  1) 같은 course + 같은 section + 같은 난이도
  2) 같은 course + 같은 section + 난이도 ±1
  3) 같은 course + 같은 section (난이도 무시)
  4) 같은 course (인접 섹션 포함)
  → 그래도 없으면 원문제만 반환.

에러 규칙 (A · 리턴값 방식):
- 결과가 빈 리스트([])면 '잘못된 id', 원문제가 들어있으면 정상(유사 0~k개 가변).
- 유효성은 RDS(quiz_question) 존재 여부로 판별 → 방금 등록돼 아직 배치 인덱싱 전인 문제를
  '잘못된 id'로 오판하지 않는다(그 경우 [원문제]만 반환).

주의:
- difficulty 폴백은 quiz_question.difficulty 컬럼 추가(마이그레이션) 후 동작. 컬럼 없으면 자동으로 section/course 폴백만 사용.
"""
try:
    from . import db, vector_store
except ImportError:
    import db
    import vector_store


def get_similar_problems(student_id: int, problem_id: int, k: int = 2) -> list[int]:
    # student_id는 종준 계약상 받는다(현재 필터엔 미사용 — 추후 개인화/풀이이력 제외에 활용 가능).
    # 반환 규칙: 빈 리스트[]=잘못된 id, 원문제 포함=정상(유사 0~k개 가변).
    if not _exists_in_rds(problem_id):
        return []  # 잘못된 id → 빈 리스트

    meta = vector_store.retrieve_meta(problem_id)
    if meta is None:
        return [problem_id]  # 유효하지만 아직 인덱싱 전 → 원문제만

    course = meta["courseId"]
    section = meta["sectionId"]
    diff = meta["difficulty"]

    exclude: set[int] = {problem_id}   # 원문제 자신은 유사 후보에서 제외
    found: list[int] = []
    for spec in _fallback_specs(course, section, diff):
        if len(found) >= k:
            break
        for pid in vector_store.search(problem_id, spec, exclude, k - len(found)):
            found.append(pid)
            exclude.add(pid)

    return [problem_id] + found[:k]


def _fallback_specs(course, section, diff) -> list[dict]:
    specs: list[dict] = []
    if section is not None and diff is not None:
        specs.append({"courseId": course, "sectionId": section, "difficulty": diff})
        specs.append({"courseId": course, "sectionId": section, "difficulty_range": (diff - 1, diff + 1)})
    if section is not None:
        specs.append({"courseId": course, "sectionId": section})
    specs.append({"courseId": course})
    return specs


def _exists_in_rds(problem_id: int) -> bool:
    """문제가 실제로 존재하는지 RDS로 확인 (잘못된 id 판별용)."""
    conn = db.get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM quiz_question WHERE question_id = %s", (problem_id,))
            return cur.fetchone() is not None
    finally:
        conn.close()
