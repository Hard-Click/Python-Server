"""추천 엔드포인트 (경량). 인덱싱은 indexer.py 배치가 담당한다.

실행:  uvicorn app:app --host 0.0.0.0 --port 8000
"""
from fastapi import FastAPI
from pydantic import BaseModel, Field

import vector_store

app = FastAPI(title="Quiz Recommender")


class RecommendRequest(BaseModel):
    wrongQuestionIds: list[int]                              # 이번에 틀린 문제
    courseId: int                                            # 같은 강의 안에서만 추천
    excludeQuestionIds: list[int] = Field(default_factory=list)  # 이미 푼 문제 등
    perQuestion: int = 2                                     # 틀린 문제당 추천 개수


class Recommendation(BaseModel):
    wrongQuestionId: int
    similarQuestionIds: list[int]


class RecommendResponse(BaseModel):
    recommendations: list[Recommendation]


@app.on_event("startup")
def _startup() -> None:
    vector_store.ensure_collection()


@app.post("/quiz/reviews/recommend", response_model=RecommendResponse)
def recommend(req: RecommendRequest) -> RecommendResponse:
    exclude = set(req.wrongQuestionIds) | set(req.excludeQuestionIds)

    recs: list[Recommendation] = []
    for wid in req.wrongQuestionIds:
        try:
            similar = vector_store.search_similar(wid, req.courseId, exclude, req.perQuestion)
        except Exception:
            similar = []  # 아직 배치가 인덱싱 안 한 문제 → 조용히 빈 결과
        recs.append(Recommendation(wrongQuestionId=wid, similarQuestionIds=similar))

    return RecommendResponse(recommendations=recs)


@app.get("/health")
def health() -> dict:
    return {"ok": True}
