"""application/ports.py의 인터페이스를 실제 RDS 쿼리로 구현.

⚠️ 테이블/컬럼명은 PO 설계 문서 기준 추정치임. DBA가 실제 마이그레이션 확정하면
   이 파일의 SQL만 고치면 되고, use_cases.py/domain/은 전혀 안 건드려도 됨
   (Clean Architecture로 나눈 이유가 바로 이거 - 스키마 변경의 영향범위를 여기로 가둠).
"""
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


class MySQLScheduleRepository:
    def save_weekly_schedule(self, enrollment_id: str, week_no: int, assignment: dict) -> None:
        sql = """
            INSERT INTO weekly_schedule (enrollment_id, week_no, generated_at)
            VALUES (%s, %s, NOW())
        """
        slot_sql = """
            INSERT INTO schedule_slot (weekly_schedule_id, lecture_id, plan_week, status)
            VALUES (%s, %s, %s, 'planned')
        """
        with get_connection() as conn, conn.cursor() as cur:
            cur.execute(sql, (enrollment_id, week_no))
            schedule_id = cur.lastrowid
            for lecture_id, plan_week in assignment.items():
                cur.execute(slot_sql, (schedule_id, lecture_id, plan_week))


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


class MySQLActivityRepository:
    def get_recency_and_streak(self, enrollment_id: str) -> tuple:
        sql = """
            SELECT
              DATEDIFF(CURDATE(), MAX(CASE WHEN achieved THEN date END)) AS recency_days,
              (SELECT COUNT(*) FROM (
                 SELECT date FROM daily_achievement
                 WHERE enrollment_id = %s AND achieved = FALSE
                 ORDER BY date DESC LIMIT 30
               ) recent_misses) AS miss_streak_days
            FROM daily_achievement
            WHERE enrollment_id = %s
        """
        with get_connection() as conn, conn.cursor() as cur:
            cur.execute(sql, (enrollment_id, enrollment_id))
            row = cur.fetchone()
            return (row["recency_days"] or 0, row["miss_streak_days"] or 0)


class MySQLRiskRepository:
    def save_risk_score(self, enrollment_id: str, score: float, label: str) -> None:
        sql = """
            INSERT INTO dropout_risk (enrollment_id, computed_at, risk_score, method, features)
            VALUES (%s, NOW(), %s, 'rule', JSON_OBJECT('label', %s))
        """
        with get_connection() as conn, conn.cursor() as cur:
            cur.execute(sql, (enrollment_id, score, label))


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
            WHERE ws.enrollment_id = %s AND ss.status = 'planned'
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
        sql = "SELECT daily_cap_min FROM enrollment_onboarding WHERE enrollment_id = %s"
        with get_connection() as conn, conn.cursor() as cur:
            cur.execute(sql, (enrollment_id,))
            row = cur.fetchone()
            return row["daily_cap_min"] if row else 60  # 폴백 기본값

    def save_day_assignment(self, enrollment_id: str, assignment: dict) -> None:
        sql = """
            UPDATE schedule_slot ss
            JOIN weekly_schedule ws ON ws.id = ss.weekly_schedule_id
            SET ss.plan_date = DATE_ADD(CURDATE(), INTERVAL %s + 1 DAY)
            WHERE ws.enrollment_id = %s AND ss.lecture_id = %s
        """
        with get_connection() as conn, conn.cursor() as cur:
            for lesson_id, day_offset in assignment.items():
                cur.execute(sql, (day_offset, enrollment_id, lesson_id))
