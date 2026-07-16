"""온보딩 즉시생성용 경량 엔드포인트. 나머지(야간/주간)는 jobs/의 크론으로 처리."""
from flask import Flask, request, jsonify
from application.use_cases import (
    GenerateWeeklyScheduleUseCase, SummarizeShadowDecisionsUseCase,
    EFFICIENCY_STRETCH_EXPERIMENT_NAME,
)
from infrastructure.repositories import (
    MySQLLessonRepository, MySQLDiagnosticScoreRepository, MySQLScheduleRepository,
    MySQLSubscriptionRepository, MySQLLessonProgressRepository,
    MySQLStudentNotificationRepository, MySQLExperimentRepository,
    MySQLCourseLearningPolicyRepository,
)

app = Flask(__name__)

# GenerateWeeklyScheduleUseCase는 7개 repo가 전부 필요(weekly_reflow.py와 동일 배선) -
# 온보딩 즉시생성도 효율계수·연장·알림·실험(shadow) 로직을 그대로 타므로 같은 의존성을 받는다.
use_case = GenerateWeeklyScheduleUseCase(
    lesson_repo=MySQLLessonRepository(),
    diagnostic_repo=MySQLDiagnosticScoreRepository(),
    schedule_repo=MySQLScheduleRepository(),
    subscription_repo=MySQLSubscriptionRepository(),
    lesson_progress_repo=MySQLLessonProgressRepository(),
    notification_repo=MySQLStudentNotificationRepository(),
    experiment_repo=MySQLExperimentRepository(),
    course_policy_repo=MySQLCourseLearningPolicyRepository(),
)

# 관리자용 shadow 집계 조회(읽기 전용). 실측 전 shadow 로그를 화면/외부에서 볼 때 사용.
shadow_summary_use_case = SummarizeShadowDecisionsUseCase(MySQLExperimentRepository())


@app.post("/generate-preview")
def generate_preview():
    """미리보기: CP-SAT 계산만 하고 저장/알림/실험로그는 남기지 않음(commit=False).
    preview 클릭이 DB·shadow 로그를 오염시키지 않게 확정 경로와 완전히 분리."""
    body = request.get_json()
    result = use_case.execute(
        member_id=body["member_id"],
        enrollments=body["enrollments"],
        total_weekly_minutes=body["total_weekly_minutes"],
        commit=False,
        study_days=body.get("study_days"),  # 있으면 코스별 강도 상한 적용, 없으면 미적용
    )
    return jsonify(result)


@app.post("/generate-commit")
def generate_commit():
    """온보딩 즉시생성(확정): 스케줄 저장·알림·실험로그까지 수행(commit=True)."""
    body = request.get_json()
    result = use_case.execute(
        member_id=body["member_id"],
        enrollments=body["enrollments"],
        total_weekly_minutes=body["total_weekly_minutes"],
        commit=True,
        study_days=body.get("study_days"),  # 있으면 코스별 강도 상한 적용, 없으면 미적용
    )
    return jsonify(result)


@app.get("/admin/shadow-summary")
def shadow_summary():
    """shadow mode 결정 로그 집계(읽기 전용). ?experiment=... 로 실험명 지정 가능.
    성과 증거가 아니라 '배정 variant를 적용했다면 결정이 얼마나 달라졌을지' 정책 변화량 관측용."""
    experiment_name = request.args.get("experiment", EFFICIENCY_STRETCH_EXPERIMENT_NAME)
    return jsonify(shadow_summary_use_case.execute(experiment_name))


@app.get("/health")
def health():
    return jsonify({"ok": True})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
