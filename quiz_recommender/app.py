"""로컬 테스트용 경량 HTTP 래퍼 (선택).

실제 통합은 종준 FSRS가 recommender.get_similar_problems()를 함수로 직접 호출한다.
이 파일은 개발 중 curl로 손쉽게 확인하려는 용도일 뿐, 프로덕션 통합 경로가 아니다.

실행:  uvicorn app:app --port 8000
확인:  GET /quiz/similar/{problem_id}?k=2
"""
from fastapi import FastAPI

try:
    from . import vector_store
    from .recommender import get_similar_problems
except ImportError:
    import vector_store
    from recommender import get_similar_problems

app = FastAPI(title="Quiz Recommender (test harness)")


@app.on_event("startup")
def _startup() -> None:
    vector_store.ensure_collection()


@app.get("/quiz/similar/{problem_id}")
def similar(problem_id: int, student_id: int = 0, k: int = 2) -> dict:
    return {"problems": get_similar_problems(student_id, problem_id, k)}   # [원문제, 유사...]


@app.get("/health")
def health() -> dict:
    return {"ok": True}
