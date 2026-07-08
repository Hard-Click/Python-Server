"""온보딩 즉시생성용 경량 엔드포인트. 나머지(야간/주간)는 jobs/의 크론으로 처리."""
from flask import Flask, request, jsonify
from application.use_cases import GenerateWeeklyScheduleUseCase
from infrastructure.repositories import (
    MySQLLessonRepository, MySQLDiagnosticScoreRepository, MySQLScheduleRepository,
)

app = Flask(__name__)

use_case = GenerateWeeklyScheduleUseCase(
    lesson_repo=MySQLLessonRepository(),
    diagnostic_repo=MySQLDiagnosticScoreRepository(),
    schedule_repo=MySQLScheduleRepository(),
)


@app.post("/generate-preview")
def generate_preview():
    body = request.get_json()
    result = use_case.execute(
        member_id=body["member_id"],
        enrollments=body["enrollments"],
        total_weekly_minutes=body["total_weekly_minutes"],
    )
    return jsonify(result)


@app.get("/health")
def health():
    return jsonify({"ok": True})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
