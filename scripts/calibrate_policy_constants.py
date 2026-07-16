"""EFFICIENCY_STRETCH_FACTOR A/B 실험 결과 분석 (배정/노출 인프라는 완료, 성과 조인은 TODO).

배정 인프라는 이미 있다: domain/experiments.py::assign_variant()가 학생마다
member_id 해시로 [0.3, 0.5, 0.7] 중 하나를 결정적으로 배정하고,
application/use_cases.py::GenerateWeeklyScheduleUseCase가 매주 그 variant로
효율계수를 계산하면서 infrastructure/repositories.py::MySQLExperimentRepository로
(member_id, variant, 노출시각)을 experiment_exposure 테이블에 기록한다.

아직 없는 건 "성과" 쪽이다 - 완주율·평균 성적 향상 같은 결과 지표가 별도 테이블/집계로
존재해야 exposure와 조인해서 그룹별 비교가 가능한데, 그 성과 집계 자체가 아직 시스템에
없다(성적 트래킹은 Module05 스코프 밖이었음). 그래서 analyze_stretch_factor_ab_test()는
여전히 TODO - "배정은 끝났다, 성과 데이터가 생기면 이 함수를 채운다"는 상태.

실행: python -m scripts.calibrate_policy_constants
"""
import sys


def analyze_stretch_factor_ab_test(group_results):
    """A/B 테스트 결과를 받아 최적 스트레치 팩터를 추천 (TODO: 성과 데이터 집계가 아직 없어서 미구현).

    group_results 예상 형태: [{"stretch_factor": 0.3, "completion_rate": ..., "avg_score_delta": ...}, ...]
    - experiment_exposure 테이블(variant별 노출)과 완주율/성적 데이터를 member_id로 조인해서
      이 형태로 집계하는 쿼리가 먼저 필요함. 그 집계가 생기면 여기서 그룹별 평균/유의성 검정.
    """
    raise NotImplementedError(
        "성과(완주율/성적) 집계가 아직 없음. 노출 기록(experiment_exposure)은 이미 쌓이고 있으니 "
        "그것과 조인할 성과 데이터가 준비되면 이 함수를 채울 것. docs/policy_constants.md 참고."
    )


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    print("배정/노출 인프라는 이미 동작 중: domain/experiments.py + experiment_exposure 테이블.")
    print("EFFICIENCY_STRETCH_FACTOR(도메인 기본값 0.5)는 지금 [0.3, 0.5, 0.7] 중 하나로 학생별 배정됨.")
    print("남은 건 성과(완주율/성적) 집계와의 조인 - docs/policy_constants.md 참고.")


if __name__ == "__main__":
    main()
