"""quiz_recommender 패키지 — 추천 진입점 노출.

종준 FSRS에서 사용:
    from quiz_recommender import get_similar_problems
    ids = get_similar_problems(student_id, problem_id, k=2)   # [원문제, 유사1, 유사2]
    #  []        = 잘못된 id
    #  [원문제]  = 유사 없음(정상). result[1:]가 유사문제.

주의:
- import 시 config가 같은 폴더의 .env에서 QDRANT_URL/QDRANT_API_KEY를 읽는다.
  (.env가 없으면 실제 환경변수 사용)
- QDRANT_URL/QDRANT_API_KEY가 없어도 import는 항상 통과한다 — 그 상태로 호출하면
  런타임 장애와 동일하게 [원문제]를 반환한다(계약은 리턴값으로만 표현, 예외 없음).
- 추천 경로는 Gemini 키가 없어도 import·동작한다. (임베딩은 인덱싱 배치에서만 사용)
"""
try:
    from .recommender import get_similar_problems
except ImportError:                      # 폴더 안에서 직접 실행하는 경우
    from recommender import get_similar_problems

__all__ = ["get_similar_problems"]
