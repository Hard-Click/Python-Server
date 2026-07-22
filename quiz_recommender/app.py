"""유사문제 추천 HTTP 서빙 API (프로덕션 경로).

백엔드(Spring) SimilarProblemRecommenderAdapter가 quiz.ai.* 설정으로 이 서버를 호출한다.
  GET /quiz/similar/{problem_id}?student_id=&k=  →  {"problems": [원문제, 유사...]}
(초기 설계는 종준 FSRS 배치의 함수 직접 호출이었으나, 실제 제품은 학생이 유사퀴즈
화면에 진입할 때 백엔드가 실시간 HTTP 호출하는 구조로 확정 — 2026-07-20)

운영: monitoring EC2에서 systemd(quiz-recommender.service)로 상시 구동.
실행:  uvicorn app:app --host 0.0.0.0 --port 8000
"""
from fastapi import FastAPI

try:
    from . import vector_store
    from .recommender import get_similar_problems
    from .review import recommend_review
except ImportError:
    import vector_store
    from recommender import get_similar_problems
    from review import recommend_review

app = FastAPI(title="Quiz Recommender")


@app.on_event("startup")
def _startup() -> None:
    vector_store.ensure_collection()


@app.get("/quiz/similar/{problem_id}")
def similar(problem_id: int, student_id: int = 0, k: int = 2) -> dict:
    return {"problems": get_similar_problems(student_id, problem_id, k)}   # [원문제, 유사...]


@app.get("/quiz/review/{student_id}")
def review(student_id: int, k: int = 2) -> dict:
    """학생 복습 세트 — 원문제(무엇을 복습할지)를 이력에서 선정하고 각 원문제의
    유사문제 k개를 붙여 급한 순으로 반환. 콜드스타트(이력 없음)면 {"reviews": []}.
    /quiz/similar 와 달리 원문제를 호출자가 안 넘긴다 — 선정까지 여기서 한다(정책 '안 B')."""
    return {"reviews": recommend_review(student_id, k)}


@app.post("/quiz/submissions")
def submissions(payload: dict) -> dict:
    """백엔드 QuizSubmissionAiAdapter의 제출 전송 수신 스텁.

    개인화 신호는 이 payload가 아니라 공유 RDS(quiz_submission_answer)를 직접 읽으므로
    저장 없이 수신 확인만 한다 — quiz.ai.enabled=true일 때 백엔드 404 에러 로그 방지용.
    """
    return {"received": True}


@app.get("/health")
def health() -> dict:
    return {"ok": True}
