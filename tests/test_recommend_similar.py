"""RecommendSimilarProblemsUseCase - 추천기/오답조회를 fake로 두고 BE 계약 해석만 검증(DB 불필요).

BE(quiz_recommender) 계약:
  []              = 잘못된 problem_id
  [원문제]          = 유사 0개(RDS/indexer 미가동 시 포함)
  [원문제, 유사...]  = result[0]=원문제, result[1:]=유사문제
"""
from application.use_cases import RecommendSimilarProblemsUseCase


class FakeWrongAnswerRepo:
    def __init__(self, wrong):
        self._wrong = wrong

    def get_wrong_question_ids(self, student_id, quiz_id):
        return self._wrong


class FakeRecommender:
    """problem_id -> 반환리스트 매핑. 매핑에 없는 id는 []([잘못된 id])로 취급."""
    def __init__(self, mapping):
        self._mapping = mapping
        self.calls = []

    def get_similar_problems(self, student_id, problem_id, k=2):
        self.calls.append((student_id, problem_id, k))
        return self._mapping.get(problem_id, [])


def test_recommends_similar_for_each_wrong_question():
    repo = FakeWrongAnswerRepo([101, 102])
    rec = FakeRecommender({101: [101, 201, 202], 102: [102, 301]})
    uc = RecommendSimilarProblemsUseCase(repo, rec)
    result = uc.execute(student_id=7, quiz_id=55, k=2)
    # result[0](원문제) 버리고 유사문제만 남는다
    assert result == {101: [201, 202], 102: [301]}


def test_empty_list_means_invalid_id_and_is_skipped():
    repo = FakeWrongAnswerRepo([999])
    rec = FakeRecommender({})  # 999 -> []
    uc = RecommendSimilarProblemsUseCase(repo, rec)
    assert uc.execute(student_id=1, quiz_id=1) == {}


def test_original_only_means_no_similar_and_is_skipped():
    # RDS/indexer 미가동 시 [원문제]만 나오는 상황 - 추천 없음으로 조용히 스킵
    repo = FakeWrongAnswerRepo([101])
    rec = FakeRecommender({101: [101]})
    uc = RecommendSimilarProblemsUseCase(repo, rec)
    assert uc.execute(student_id=1, quiz_id=1) == {}


def test_no_wrong_answers_returns_empty():
    uc = RecommendSimilarProblemsUseCase(FakeWrongAnswerRepo([]), FakeRecommender({}))
    assert uc.execute(student_id=1, quiz_id=1) == {}


def test_mixed_valid_invalid_and_empty_similar():
    repo = FakeWrongAnswerRepo([101, 102, 103])
    rec = FakeRecommender({101: [101, 201], 102: [], 103: [103]})
    uc = RecommendSimilarProblemsUseCase(repo, rec)
    # 101만 추천 있음, 102=잘못된 id, 103=유사0개
    assert uc.execute(student_id=1, quiz_id=1) == {101: [201]}


def test_student_id_and_k_passed_through_per_contract():
    repo = FakeWrongAnswerRepo([101])
    rec = FakeRecommender({101: [101, 201]})
    uc = RecommendSimilarProblemsUseCase(repo, rec)
    uc.execute(student_id=42, quiz_id=9, k=3)
    assert rec.calls == [(42, 101, 3)]
