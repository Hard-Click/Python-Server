"""Shadow mode 결정 로그 관리자 리포트 - experiment_shadow_decision을 조회해 요약 출력.

'배정 variant를 실제로 적용했다면 정책 결정이 얼마나 달라졌을지'만 본다(성과 증거 아님 -
실사용자 없이 성과는 못 잼). 조회=포트, 집계=domain/shadow_report.py(순수).

실행: python -m scripts.report_shadow_decisions   (DB 연결 환경변수 필요)
"""
import sys

from application.use_cases import (
    SummarizeShadowDecisionsUseCase, EFFICIENCY_STRETCH_EXPERIMENT_NAME,
)
from domain.shadow_report import format_summary_lines
from infrastructure.repositories import MySQLExperimentRepository


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    use_case = SummarizeShadowDecisionsUseCase(MySQLExperimentRepository())
    summary = use_case.execute(EFFICIENCY_STRETCH_EXPERIMENT_NAME)
    print("\n".join(format_summary_lines(summary)))
    print("\n※ 이건 정책 변화량·과부하 가능성 관측이지 성과(점수 향상) 증거가 아니다.")


if __name__ == "__main__":
    main()
