"""application/ports.py의 인터페이스를 실제 RDS 쿼리로 구현.

⚠️ 테이블/컬럼명은 PO 설계 문서 기준 추정치임. DBA가 실제 마이그레이션 확정하면
   이 파일의 SQL만 고치면 되고, use_cases.py/domain/은 전혀 안 건드려도 됨
   (Clean Architecture로 나눈 이유가 바로 이거 - 스키마 변경의 영향범위를 여기로 가둠).
"""
import json
import uuid

from domain.review import Card
from infrastructure.db import get_connection


class MySQLLessonRepository:
    def get_lessons_for_course(self, course_id: str) -> list[dict]:
        sql = """
            SELECT id, expected_duration_min AS duration_min, NULL AS deadline_week
            FROM lecture
            WHERE course_id = %s
            ORDER BY sequence_order
        """
        with get_connection() as conn, conn.cursor() as cur:
            cur.execute(sql, (course_id,))
            return cur.fetchall()

    def get_prerequisites(self, course_id: str) -> list[tuple]:
        sql = """
            SELECT lecture_id, prerequisite_lecture_id
            FROM lecture_prerequisite lp
            JOIN lecture l ON l.id = lp.lecture_id
            WHERE l.course_id = %s
        """
        with get_connection() as conn, conn.cursor() as cur:
            cur.execute(sql, (course_id,))
            return [(row["prerequisite_lecture_id"], row["lecture_id"]) for row in cur.fetchall()]


class MySQLCourseLearningPolicyRepository:
    """강사가 코스 등록 시 정한 코스별 학습 정책 조회(읽기 전용).
    daily_recommended_minutes = 화면 '코스별 강도 상한(하루 최대 학습 시간)'(PR#1이 여기에 씀).
    use_case가 이걸 학습일수로 곱해 주간 상한(course_weekly_caps)으로 CP-SAT에 넘긴다."""

    def get_daily_max_minutes(self, course_ids: list[str]) -> dict:
        if not course_ids:
            return {}
        placeholders = ",".join(["%s"] * len(course_ids))
        sql = f"""
            SELECT course_id, daily_recommended_minutes
            FROM course_learning_policy
            WHERE course_id IN ({placeholders}) AND daily_recommended_minutes IS NOT NULL
        """
        with get_connection() as conn, conn.cursor() as cur:
            cur.execute(sql, tuple(course_ids))
            return {row["course_id"]: row["daily_recommended_minutes"] for row in cur.fetchall()}


class MySQLDiagnosticScoreRepository:
    def get_grades_for_student(self, member_id: str, course_ids: list[str]) -> dict:
        if not course_ids:
            return {}
        placeholders = ",".join(["%s"] * len(course_ids))
        sql = f"""
            SELECT course_id, grade
            FROM student_diagnostic_score
            WHERE member_id = %s AND course_id IN ({placeholders})
        """
        with get_connection() as conn, conn.cursor() as cur:
            cur.execute(sql, (member_id, *course_ids))
            return {row["course_id"]: row["grade"] for row in cur.fetchall()}


class MySQLStudentCapRepository:
    """학생레벨 cap이므로 member_id 기준으로 묶는다(설계상 "학생레벨 cap 채택(코스레벨 폐기)").
    하루 상한 daily_cap_min은 student_capacity(student_id=member_id) - V3.1.8에서
    enrollment_onboarding에서 이리로 이동됨. 쉬는날은 enrollment_onboarding.rest_days
    (비트마스크, bit0=일 … bit6=토)에 남아있음(rest_days_per_week 아님). study_days = 7 - 쉬는날 수."""

    DEFAULT_WEEKLY_AVAILABLE_MINUTES = 420  # 온보딩 미완료 콜드스타트 폴백 - 관리자 전역정책값 후보
    DEFAULT_STUDY_DAYS = 6  # 쉬는날 정보 없을 때 폴백(주 1일 휴식 가정)

    def _fetch_cap_and_rest(self, member_id: str):
        # daily_cap은 student_capacity(enrollment 무관), 쉬는날 rest_days는 활성 enrollment의
        # onboarding에서 가져온다. onboarding이 없어도 cap은 반환되도록 서브쿼리로 LEFT 결합.
        sql = """
            SELECT sc.daily_cap_min,
                   (SELECT eo.rest_days
                      FROM enrollment e
                      JOIN enrollment_onboarding eo ON eo.enrollment_id = e.enrollment_id
                     WHERE e.member_id = sc.student_id AND e.status = 'IN_PROGRESS'
                     LIMIT 1) AS rest_days
            FROM student_capacity sc
            WHERE sc.student_id = %s
            LIMIT 1
        """
        with get_connection() as conn, conn.cursor() as cur:
            cur.execute(sql, (member_id,))
            return cur.fetchone()

    @staticmethod
    def _study_days_from_rest(rest_days) -> int:
        rest_count = bin(int(rest_days)).count("1") if rest_days else 0
        return max(1, 7 - rest_count)

    def get_weekly_available_minutes(self, member_id: str) -> int:
        row = self._fetch_cap_and_rest(member_id)
        if row is None or row["daily_cap_min"] is None:
            return self.DEFAULT_WEEKLY_AVAILABLE_MINUTES
        return row["daily_cap_min"] * self._study_days_from_rest(row["rest_days"])

    def get_study_days(self, member_id: str) -> int:
        row = self._fetch_cap_and_rest(member_id)
        if row is None:
            return self.DEFAULT_STUDY_DAYS
        return self._study_days_from_rest(row["rest_days"])


class MySQLLessonProgressRepository:
    """⚠️ 추정 스키마: study_timer 도메인의 lesson_progress(실제 학습시간 기록)에
    lecture.expected_duration_min을 조인 - 강사 추정치 대 실제 소요시간 비교용.
    course_id도 같이 반환 - 과목마다 학생의 학습속도가 다를 수 있어 코스별로 계수를 분리해야 함
    (수학은 느리고 영어는 빠른 학생 등 - 학생 전체 단일 계수로는 이런 편차를 못 잡음)."""

    def get_completed_lesson_durations(self, member_id: str) -> list:
        sql = """
            SELECT l.course_id, l.expected_duration_min, lp.actual_duration_min
            FROM lesson_progress lp
            JOIN enrollment e ON e.id = lp.enrollment_id
            JOIN lecture l ON l.id = lp.lecture_id
            WHERE e.member_id = %s AND lp.completed_at IS NOT NULL
        """
        with get_connection() as conn, conn.cursor() as cur:
            cur.execute(sql, (member_id,))
            return cur.fetchall()


class MySQLScheduleRepository:
    def save_weekly_schedule(self, enrollment_id: str, week_no: int, assignment: dict) -> None:
        """⚠️ 추정 스키마(is_active, generated_batch_id 컬럼 신규 - 마이그레이션 확정 필요).

        '활성 계획 1개' 모델: 매 재생성마다 누적 삽입하면 중복 계획이 쌓이므로, 같은
        (enrollment, week_no)의 기존 활성 계획을 먼저 비활성화하고 새 계획을 is_active로 넣는다.
        학생은 계획을 직접 편집하지 않고 실측만 기록 → 시스템이 재생성하므로 계획은 시스템 소유다.
        generated_batch_id로 어느 생성 배치에서 나왔는지 추적(과거 버전은 비활성으로 남아 감사 가능)."""
        deactivate_sql = """
            UPDATE weekly_schedule SET is_active = FALSE
            WHERE enrollment_id = %s AND week_no = %s AND is_active = TRUE
        """
        insert_sql = """
            INSERT INTO weekly_schedule (enrollment_id, week_no, generated_batch_id, is_active, generated_at)
            VALUES (%s, %s, %s, TRUE, NOW())
        """
        slot_sql = """
            INSERT INTO schedule_slot (weekly_schedule_id, lecture_id, plan_week, status)
            VALUES (%s, %s, %s, 'planned')
        """
        batch_id = uuid.uuid4().hex
        with get_connection() as conn, conn.cursor() as cur:
            cur.execute(deactivate_sql, (enrollment_id, week_no))
            cur.execute(insert_sql, (enrollment_id, week_no, batch_id))
            schedule_id = cur.lastrowid
            for lecture_id, plan_week in assignment.items():
                cur.execute(slot_sql, (schedule_id, lecture_id, plan_week))


class MySQLStudentNotificationRepository:
    """⚠️ 추정 스키마: notification 테이블에 학생용 배너를 적재 - 프론트가 조회해서 표시.
    error_router_client.py(내부 운영진 Slack 알림)와는 완전히 다른 채널이니 섞지 말 것."""

    def notify_schedule_extended(self, member_id: str, extended_weeks: int) -> None:
        message = f"이번 주 스케줄이 너무 촘촘해서 완주 목표를 {extended_weeks}주 뒤로 조정했어요."
        self._insert(member_id, "SCHEDULE_EXTENDED", message)

    def notify_schedule_infeasible(self, member_id: str) -> None:
        message = "지금 설정으로는 수능 전까지 완주가 어려워요. 목표기간이나 학습량을 조정해주세요."
        self._insert(member_id, "SCHEDULE_INFEASIBLE", message)

    def _insert(self, member_id: str, notification_type: str, message: str) -> None:
        sql = """
            INSERT INTO notification (member_id, type, message, created_at)
            VALUES (%s, %s, %s, NOW())
        """
        with get_connection() as conn, conn.cursor() as cur:
            cur.execute(sql, (member_id, notification_type, message))


class MySQLExperimentRepository:
    """⚠️ 추정 스키마: experiment_exposure 테이블에 이번 배치에서 학생이 어떤 variant를
    받았는지 적재만 함 - 배정 로직 자체는 domain/experiments.py(결정적 해시)라 여기선
    "기록"만 담당. 나중에 성적/완주율 데이터와 member_id+experiment_name으로 조인해서
    scripts/calibrate_policy_constants.py의 A/B 분석에 씀."""

    def log_exposure(self, member_id: str, experiment_name: str, variant) -> None:
        sql = """
            INSERT INTO experiment_exposure (member_id, experiment_name, variant, exposed_at)
            VALUES (%s, %s, %s, NOW())
        """
        with get_connection() as conn, conn.cursor() as cur:
            cur.execute(sql, (member_id, experiment_name, str(variant)))

    def log_shadow_decision(self, member_id: str, experiment_name: str, decision: dict) -> None:
        """⚠️ 추정 스키마: experiment_shadow_decision 테이블에 shadow mode 결정 델타를 적재.
        decision 전체는 JSON 컬럼에, 조회·집계 편의를 위해 자주 쓰는 몇 개만 별도 컬럼으로 승격.
        실제 마이그레이션 확정 시 컬럼명 검증 필요(다른 repo와 동일한 추정 스키마 원칙)."""
        sql = """
            INSERT INTO experiment_shadow_decision
                (member_id, experiment_name, variant, extension_delta,
                 weekly_minutes_delta, schedule_would_change, detail, logged_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
        """
        with get_connection() as conn, conn.cursor() as cur:
            cur.execute(sql, (
                member_id, experiment_name, str(decision.get("variant")),
                decision.get("extension_delta"), decision.get("weekly_minutes_delta"),
                bool(decision.get("schedule_would_change")), json.dumps(decision, ensure_ascii=False),
            ))

    def get_shadow_decisions(self, experiment_name: str) -> list:
        """⚠️ 추정 스키마: detail(JSON) 컬럼을 그대로 복원해 결정 dict 리스트로 반환.
        집계 로직은 domain/shadow_report.py(순수)가 담당 - 여기선 조회만."""
        sql = "SELECT detail FROM experiment_shadow_decision WHERE experiment_name = %s"
        with get_connection() as conn, conn.cursor() as cur:
            cur.execute(sql, (experiment_name,))
            rows = cur.fetchall()
        return [json.loads(row["detail"]) for row in rows]


class MySQLReviewCardRepository:
    def get_card(self, enrollment_id: str, lesson_id: str):
        sql = """
            SELECT stability, difficulty, due, state, reps, lapses
            FROM review_card
            WHERE enrollment_id = %s AND lecture_id = %s
        """
        with get_connection() as conn, conn.cursor() as cur:
            cur.execute(sql, (enrollment_id, lesson_id))
            row = cur.fetchone()
            if row is None:
                return None
            card = Card()
            card.stability = row["stability"]
            card.difficulty = row["difficulty"]
            card.due = row["due"]
            return card

    def save_card(self, enrollment_id: str, lesson_id: str, card: Card) -> None:
        sql = """
            INSERT INTO review_card (enrollment_id, lecture_id, stability, difficulty, due, state)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
              stability = VALUES(stability), difficulty = VALUES(difficulty),
              due = VALUES(due), state = VALUES(state)
        """
        with get_connection() as conn, conn.cursor() as cur:
            cur.execute(sql, (enrollment_id, lesson_id, card.stability, card.difficulty, card.due, str(card.state)))


class MySQLSubscriptionRepository:
    """⚠️ 추정 스키마: enrollment -> member -> subscription.suneung_date (PO 설계 문서 기준)."""

    def get_suneung_date(self, enrollment_id: str):
        sql = """
            SELECT s.suneung_date
            FROM enrollment e
            JOIN subscription s ON s.member_id = e.member_id
            WHERE e.id = %s
        """
        with get_connection() as conn, conn.cursor() as cur:
            cur.execute(sql, (enrollment_id,))
            row = cur.fetchone()
            return row["suneung_date"] if row else None


class MySQLQuizScoreRepository:
    def get_latest_quiz_score(self, enrollment_id: str, lesson_id: str):
        sql = """
            SELECT score_percent
            FROM quiz_attempt
            WHERE enrollment_id = %s AND lecture_id = %s
            ORDER BY submitted_at DESC
            LIMIT 1
        """
        with get_connection() as conn, conn.cursor() as cur:
            cur.execute(sql, (enrollment_id, lesson_id))
            row = cur.fetchone()
            return row["score_percent"] if row else None

    def get_average_quiz_score(self, enrollment_id: str):
        sql = """
            SELECT AVG(score_percent) AS avg_score
            FROM quiz_attempt
            WHERE enrollment_id = %s
        """
        with get_connection() as conn, conn.cursor() as cur:
            cur.execute(sql, (enrollment_id,))
            row = cur.fetchone()
            return float(row["avg_score"]) if row and row["avg_score"] is not None else None


class MySQLWrongAnswerRepository:
    """학생의 특정 퀴즈 '최신 제출'에서 틀린 question_id 목록.

    quiz_submission(제출 헤더) -> quiz_submission_answer(문항별 정오) 조인.
    ⚠️ 이 두 테이블은 seed_demo.py가 쓰는 실제 스키마 기준 - MySQLQuizScoreRepository의
       quiz_attempt(추정 스키마)와 다름. 같은 학생이 같은 퀴즈를 여러 번 냈으면 submitted_at이
       가장 최신인 제출만 본다(재응시 이전 오답이 섞이지 않게).
    반환 question_id가 곧 추천기 problem_id (BE 확인: 같은 id 공간, 매핑 불필요).
    """

    def get_wrong_question_ids(self, student_id: int, quiz_id: int) -> list[int]:
        sql = """
            SELECT a.question_id
            FROM quiz_submission s
            JOIN quiz_submission_answer a ON a.submission_id = s.submission_id
            WHERE s.member_id = %s AND s.quiz_id = %s AND a.is_correct = 0
              AND s.submitted_at = (
                  SELECT MAX(s2.submitted_at) FROM quiz_submission s2
                  WHERE s2.member_id = %s AND s2.quiz_id = %s
              )
            ORDER BY a.question_id
        """
        with get_connection() as conn, conn.cursor() as cur:
            cur.execute(sql, (student_id, quiz_id, student_id, quiz_id))
            return [row["question_id"] for row in cur.fetchall()]


class MySQLActivityRepository:
    def get_recency_and_streak(self, enrollment_id: str) -> tuple:
        # miss_streak = '연속' 미달 = 마지막으로 성공(achieved)한 날 이후의 미달 일수.
        # (기존엔 최근 실패 30개를 그냥 셌는데, 중간에 성공한 날이 있어도 크게 잡혀 streak 의미가
        #  아니었음 - "연속 미달"이 이탈 momentum 신호라 이렇게 고침. achieved 이력이 없으면
        #  전 기간이 연속 미달로 간주.)
        sql = """
            SELECT
              DATEDIFF(CURDATE(), MAX(CASE WHEN achieved THEN date END)) AS recency_days,
              (SELECT COUNT(*) FROM daily_achievement
                 WHERE enrollment_id = %s AND achieved = FALSE
                   AND date > COALESCE(
                     (SELECT MAX(date) FROM daily_achievement WHERE enrollment_id = %s AND achieved = TRUE),
                     '1900-01-01')
              ) AS miss_streak_days
            FROM daily_achievement
            WHERE enrollment_id = %s
        """
        with get_connection() as conn, conn.cursor() as cur:
            cur.execute(sql, (enrollment_id, enrollment_id, enrollment_id))
            row = cur.fetchone()
            return (row["recency_days"] or 0, row["miss_streak_days"] or 0)


class MySQLRiskRepository:
    def save_risk_score(
        self,
        enrollment_id: str,
        score: float,
        label: str,
        contributions: dict[str, float],
        top_reason: str,
    ) -> None:
        # 축별 기여도·최대사유를 features JSON에 함께 적재(DDL 불필요, JSON 컬럼 재사용).
        # contributions는 dict라 JSON 문자열로 직렬화 후 CAST - Java churn 도메인이 이 컬럼을 read.
        sql = """
            INSERT INTO dropout_risk (enrollment_id, computed_at, risk_score, method, features)
            VALUES (%s, NOW(), %s, 'rule',
                    JSON_OBJECT('label', %s,
                                'top_reason', %s,
                                'contributions', CAST(%s AS JSON)))
        """
        with get_connection() as conn, conn.cursor() as cur:
            cur.execute(sql, (enrollment_id, score, label, top_reason, json.dumps(contributions)))


class MySQLWeeklyProgressRepository:
    """⚠️ 야간 리플로우용 - 스키마 확정 전 추정 쿼리. DBA 확정되면 여기만 수정."""

    def get_cumulative_slip_minutes(self, enrollment_id: str) -> int:
        sql = """
            SELECT COALESCE(SUM(planned_min - actual_min), 0) AS slip
            FROM daily_achievement
            WHERE enrollment_id = %s AND achieved = FALSE
              AND date >= DATE_SUB(CURDATE(), INTERVAL 1 WEEK)
        """
        with get_connection() as conn, conn.cursor() as cur:
            cur.execute(sql, (enrollment_id,))
            return cur.fetchone()["slip"] or 0

    def get_weekly_average_minutes(self, enrollment_id: str) -> int:
        sql = """
            SELECT COALESCE(AVG(planned_min), 0) * 7 AS weekly_avg
            FROM daily_achievement
            WHERE enrollment_id = %s
        """
        with get_connection() as conn, conn.cursor() as cur:
            cur.execute(sql, (enrollment_id,))
            return int(cur.fetchone()["weekly_avg"] or 0)

    def get_remaining_lessons_this_week(self, enrollment_id: str) -> list[dict]:
        sql = """
            SELECT l.id, l.expected_duration_min AS duration_min
            FROM schedule_slot ss
            JOIN weekly_schedule ws ON ws.id = ss.weekly_schedule_id
            JOIN lecture l ON l.id = ss.lecture_id
            WHERE ws.enrollment_id = %s AND ws.is_active = TRUE AND ss.status = 'planned'
              AND ss.plan_date >= CURDATE()
              AND YEARWEEK(ss.plan_date, 1) = YEARWEEK(CURDATE(), 1)
        """
        with get_connection() as conn, conn.cursor() as cur:
            cur.execute(sql, (enrollment_id,))
            return cur.fetchall()

    def get_remaining_days_this_week(self, enrollment_id: str) -> int:
        # 이번 주 일요일 기준 오늘 이후 남은 일수 (Frozen Zone: 오늘은 이미 확정이므로 내일부터 카운트)
        sql = "SELECT 7 - WEEKDAY(CURDATE()) - 1 AS remaining_days"
        with get_connection() as conn, conn.cursor() as cur:
            cur.execute(sql)
            return max(cur.fetchone()["remaining_days"], 0)

    def get_daily_cap_minutes(self, enrollment_id: str) -> int:
        # 하루 상한은 student_capacity(학생단위) - enrollment_onboarding.daily_cap_min은 V3.1.8에서 삭제됨.
        sql = """
            SELECT sc.daily_cap_min
            FROM student_capacity sc
            JOIN enrollment e ON e.member_id = sc.student_id
            WHERE e.enrollment_id = %s
        """
        with get_connection() as conn, conn.cursor() as cur:
            cur.execute(sql, (enrollment_id,))
            row = cur.fetchone()
            return row["daily_cap_min"] if row and row["daily_cap_min"] is not None else 60  # 폴백 기본값

    def save_day_assignment(self, enrollment_id: str, assignment: dict) -> None:
        # write 범위를 get_remaining_lessons_this_week(read)와 정확히 동일하게 제한한다:
        # status='planned' + 오늘 이후 + 이번 주(YEARWEEK). 이 필터가 없으면 같은 lecture_id가
        # 지난 주차/이전 스케줄에도 있을 때 그것까지 덮어써서 Frozen Zone(지나간 확정분)을 침범함.
        sql = """
            UPDATE schedule_slot ss
            JOIN weekly_schedule ws ON ws.id = ss.weekly_schedule_id
            SET ss.plan_date = DATE_ADD(CURDATE(), INTERVAL %s + 1 DAY)
            WHERE ws.enrollment_id = %s AND ss.lecture_id = %s
              AND ws.is_active = TRUE
              AND ss.status = 'planned'
              AND ss.plan_date >= CURDATE()
              AND YEARWEEK(ss.plan_date, 1) = YEARWEEK(CURDATE(), 1)
        """
        with get_connection() as conn, conn.cursor() as cur:
            for lesson_id, day_offset in assignment.items():
                cur.execute(sql, (day_offset, enrollment_id, lesson_id))
