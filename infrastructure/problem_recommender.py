"""ProblemRecommenderPort 구현 - 같은 레포의 quiz_recommender 패키지(BE)를 감싼다.

quiz_recommender.get_similar_problems는 Qdrant를 치는 외부연동이라 여기(infrastructure)에
격리한다(domain/application은 ProblemRecommenderPort만 안다).

import를 모듈 최상단이 아니라 호출 시점에 하는 이유: quiz_recommender 패키지가 아직 안 깔린
환경(순수 도메인 테스트, RDS/Qdrant 미가동 등)에서도 이 모듈 import 자체는 실패하지 않게 하려는
것. 실제로 추천을 호출하는 순간에만 의존성이 필요하다.
"""


class QdrantProblemRecommender:
    def get_similar_problems(self, student_id: int, problem_id: int, k: int = 2) -> list[int]:
        # BE가 패키지화하면 `from quiz_recommender.recommender import get_similar_problems`로 바로 잡힘.
        from quiz_recommender.recommender import get_similar_problems
        return get_similar_problems(student_id, problem_id, k)
