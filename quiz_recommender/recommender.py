"""추천 진입 함수. 종준 FSRS가 같은 프로세스에서 직접 호출한다(HTTP 아님).

get_similar_problems(student_id, problem_id, k=2)  → 문제 id 리스트
  · 정상+유사 충분 → [원문제, 유사1, 유사2]
  · 유사 1개뿐     → [원문제, 유사1]
  · 유사 없음       → [원문제]        (원문제 있음 = 정상)
  · 잘못된 id       → []              (빈 리스트 = 에러, 규칙 A)
  data[0]=원문제, data[1:]=유사(유사도순)

추천 순서:
  0) 개인화(personalize) — 학생 풀이 이력이 있으면 약점 단원 우선 + 난이도 사다리로 선별.
     이력이 없거나(콜드스타트) RDS 조회가 실패하면 조용히 아래 베이스라인으로 폴백.
  베이스라인 폴백(유사문제가 k개 안 채워지면 단계적으로 완화):
  1) 같은 course + 같은 section + 같은 난이도
  2) 같은 course + 같은 section + 난이도 ±1
  3) 같은 course + 같은 section (난이도 무시)
  4) 같은 course (인접 섹션 포함)
  → 그래도 없으면 원문제만 반환.
  ※ 모든 단계에 강사 격리(instructorId) 필터가 함께 걸린다 — 강사 간 문제 공유 금지 정책.
    (인덱싱이 오래된 문제는 instructorId payload가 없을 수 있음 → 그 경우만 course 격리로 동작)

에러 규칙 (A · 리턴값 방식):
- 결과가 빈 리스트([])면 '잘못된 id', 원문제가 들어있으면 정상(유사 0~k개 가변).
- 유효성은 RDS(quiz_question) 존재 여부로 판별 → 방금 등록돼 아직 배치 인덱싱 전인 문제를
  '잘못된 id'로 오판하지 않는다(그 경우 [원문제]만 반환).

주의:
- difficulty 폴백은 quiz_question.difficulty 컬럼 추가(마이그레이션) 후 동작. 컬럼 없으면 자동으로 section/course 폴백만 사용.
"""
import logging

try:
    from . import db, personalize, vector_store
except ImportError:
    import db
    import personalize
    import vector_store

logger = logging.getLogger(__name__)


def get_similar_problems(student_id: int, problem_id: int, k: int = 2) -> list[int]:
    # student_id는 개인화(약점 단원 우선·난이도 사다리)에 쓴다. 이력 없으면 베이스라인.
    # 반환 규칙(종준 FSRS와 확정):
    #   []                     = 잘못된 id          → 배치: skip
    #   [problem_id]           = 유사 없음 or 장애   → 배치: skip (복습은 그대로 진행)
    #   [problem_id, 유사...]  = 정상(유사 0~k개 가변)
    # 정책 ⓐ: RDS/Qdrant 장애는 예외를 밖으로 던지지 않고 [원문제]로 눌러 추천만 조용히 스킵한다.
    #         (종준 배치가 예외 처리를 신경 쓰지 않도록 — 계약은 '리턴값'으로만 표현)
    try:
        exists = _exists_in_rds(problem_id)
    except Exception:  # noqa: BLE001 - RDS 장애. 유효성 판단 불가하나 배치는 실제 오답 id로만 호출 → 원문제만
        logger.warning("추천 스킵(RDS 존재확인 실패) problem_id=%s", problem_id, exc_info=True)
        return [problem_id]

    if not exists:
        return []  # 잘못된 id → 빈 리스트

    try:
        return _recommend(student_id, problem_id, k)
    except Exception:  # noqa: BLE001 - Qdrant 등 추천 인프라 장애 → 추천만 스킵(복습은 진행)
        logger.warning("추천 스킵(유사문제 검색 실패) problem_id=%s", problem_id, exc_info=True)
        return [problem_id]


def _recommend(student_id: int, problem_id: int, k: int) -> list[int]:
    meta = vector_store.retrieve_meta(problem_id)
    if meta is None:
        return [problem_id]  # 유효하지만 아직 인덱싱 전 → 원문제만

    course = meta["courseId"]
    section = meta["sectionId"]
    diff = meta["difficulty"]
    instructor = meta.get("instructorId")   # 강사 격리 (옛 인덱스엔 없을 수 있음 → None 허용)

    # 0) 개인화 — 이력 기반. 실패(RDS 장애 등)·이력 없음이면 빈 리스트로 조용히 폴백.
    found: list[int] = []
    if student_id and student_id > 0:
        try:
            found = personalize.personalized_recommend(student_id, problem_id, course, k)[:k]
        except Exception:  # noqa: BLE001 - 개인화는 부가 신호. 실패해도 베이스라인은 살린다.
            logger.warning("개인화 스킵(이력 조회/재랭킹 실패) student_id=%s problem_id=%s",
                           student_id, problem_id, exc_info=True)
            found = []

    # 1~4) 베이스라인 폴백 — 개인화가 못 채운 슬롯을 유사도순으로 채움
    exclude: set[int] = {problem_id, *found}   # 원문제·이미 뽑힌 후보 제외
    for spec in _fallback_specs(course, section, diff, instructor):
        if len(found) >= k:
            break
        for pid in vector_store.search(problem_id, spec, exclude, k - len(found)):
            found.append(pid)
            exclude.add(pid)

    return [problem_id] + found[:k]


def _fallback_specs(course, section, diff, instructor=None) -> list[dict]:
    specs: list[dict] = []
    if section is not None and diff is not None:
        specs.append({"courseId": course, "sectionId": section, "difficulty": diff})
        specs.append({"courseId": course, "sectionId": section, "difficulty_range": (diff - 1, diff + 1)})
    if section is not None:
        specs.append({"courseId": course, "sectionId": section})
    specs.append({"courseId": course})
    if instructor is not None:               # 강사 격리는 완화 대상이 아님 — 전 단계 공통
        for s in specs:
            s["instructorId"] = instructor
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
