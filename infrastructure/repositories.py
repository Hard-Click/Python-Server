"""application/ports.py의 인터페이스를 실제 RDS 쿼리로 구현.

2026-07-08: 종호(DBA)가 만든 V3.1.x/V3.3.x 마이그레이션(hc-backend develop 브랜치) 기준으로
전면 수정. 이 마이그레이션들은 아직 실제 RDS에 배포 전(develop→main 배포 대기 중)이라,
배포 전까지는 이 레포의 쿼리들이 대상 테이블 없음 에러를 낼 수 있음 - 정상.
컬럼명이 배포 후 다르면 이 파일만 고치면 되고 domain/application은 안 건드려도 됨.

핵심 스키마 메모:
- enrollment의 PK는 'id'가 아니라 'enrollment_id'
- lesson은 course_id를 직접 안 가짐 - lesson.section_id -> course_section.course_id로 조인
- quiz는 lesson이 아니라 course_id+section_id 단위 -> lesson_quiz_map(N:N)으로 강의와 연결
- daily_cap은 enrollment_onboarding이 아니라 student_capacity(student_id=member_id)에 있음(다중코스 cap 정책)
- Frozen Zone: weekly_schedule.locked=1이면 리플로우 대상에서 반드시 제외
"""
from domain.review import Card
from infrastructure.db import get_connection


class MySQLLessonRepository:
    def get_lessons_for_course(self, course_id: str) -> list[dict]:
        # deadline_week는 lesson마다 없음 - course_learning_policy.recommended_duration_weeks가
        # 코스 전체의 num_weeks를 결정하므로 상위(use_case)에서 weekly_caps 길이로 이미 처리됨.
        sql = """
            SELECT l.id, l.duration_seconds / 60 AS duration_min
            FROM lesson l
            JOIN course_section cs ON cs.id = l.section_id
            WHERE cs.course_id = %s
            ORDER BY l.order_index
        """
        with get_connection() as conn, conn.cursor() as cur:
            cur.execute(sql, (course_id,))
            return cur.fetchall()

    def get_prerequisites(self, course_id: str) -> list[tuple]:
        sql = """
            SELECT lp.prerequisite_lesson_id, lp.lesson_id
            FROM lesson_prerequisite lp
            JOIN lesson l ON l.id = lp.lesson_id
            JOIN course_section cs ON cs.id = l.section_id
            WHERE cs.course_id = %s
        """
        with get_connection() as conn, conn.cursor() as cur:
            cur.execute(sql, (course_id,))
            return [(row["prerequisite_lesson_id"], row["lesson_id"]) for row in cur.fetchall()]


class MySQLDiagnosticScoreRepository:
    def get_grades_for_student(self, member_id: str, course_ids: list[str]) -> dict:
        if not course_ids:
            return {}
        placeholders = ",".join(["%s"] * len(course_ids))
        # 같은 코스라도 여러 응시일 행이 허용되므로(uq_diag_member_course_date) 최신 것만 채택
        sql = f"""
            SELECT sds.course_id, sds.grade
            FROM student_diagnostic_score sds
            INNER JOIN (
                SELECT course_id, MAX(exam_date) AS max_date
                FROM student_diagnostic_score
                WHERE member_id = %s AND course_id IN ({placeholders})
                GROUP BY course_id
            ) latest ON latest.course_id = sds.course_id AND latest.max_date = sds.exam_date
            WHERE sds.member_id = %s
        """
        with get_connection() as conn, conn.cursor() as cur:
            cur.execute(sql, (member_id, *course_ids, member_id))
            return {row["course_id"]: row["grade"] for row in cur.fetchall()}


class MySQLScheduleRepository:
    def save_weekly_schedule(self, enrollment_id: str, week_no: int, assignment: dict) -> None:
        # effective_from=오늘, locked=0(새로 생성된 스케줄은 기본 잠금 해제 - Frozen Zone은
        # 야간 배치가 "이번 주"에 locked=1을 세팅해서 다음 리플로우부터 보호하는 방식으로 운용)
        sql_schedule = """
            INSERT INTO weekly_schedule (enrollment_id, week_no, generated_at, effective_from, locked)
            VALUES (%s, %s, NOW(), CURDATE(), 0)
        """
        sql_slot = """
            INSERT INTO schedule_slot (weekly_schedule_id, lesson_id, plan_date, planned_min, status)
            VALUES (%s, %s, DATE_ADD(CURDATE(), INTERVAL %s WEEK), %s, 'PLANNED')
        """
        sql_lesson_duration = "SELECT duration_seconds / 60 AS duration_min FROM lesson WHERE id = %s"
        with get_connection() as conn, conn.cursor() as cur:
            cur.execute(sql_schedule, (enrollment_id, week_no))
            schedule_id = cur.lastrowid
            for lesson_id, week_offset in assignment.items():
                cur.execute(sql_lesson_duration, (lesson_id,))
                row = cur.fetchone()
                planned_min = row["duration_min"] if row else 0
                cur.execute(sql_slot, (schedule_id, lesson_id, week_offset, planned_min))


class MySQLReviewCardRepository:
    def get_card(self, enrollment_id: str, lesson_id: str):
        sql = """
            SELECT stability, difficulty, due
            FROM review_card
            WHERE enrollment_id = %s AND lesson_id = %s
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
            INSERT INTO review_card (enrollment_id, lesson_id, stability, difficulty, due, state)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
              stability = VALUES(stability), difficulty = VALUES(difficulty),
              due = VALUES(due), state = VALUES(state)
        """
        with get_connection() as conn, conn.cursor() as cur:
            cur.execute(sql, (enrollment_id, lesson_id, card.stability, card.difficulty, card.due, str(card.state)))


class MySQLQuizScoreRepository:
    def get_latest_quiz_score(self, enrollment_id: str, lesson_id: str):
        # quiz는 lesson_id가 없어 lesson_quiz_map(N:N)으로 연결. quiz_submission.score는
        # 태연 쪽 스케일(0~100 가정, 확인 필요) - FSRS 임계값(90/70/50)과 스케일 안 맞으면 이 함수에서 변환.
        sql = """
            SELECT qs.score
            FROM quiz_submission qs
            JOIN lesson_quiz_map lqm ON lqm.quiz_id = qs.quiz_id
            JOIN enrollment e ON e.member_id = qs.member_id
            WHERE e.enrollment_id = %s AND lqm.lesson_id = %s
            ORDER BY qs.submitted_at DESC
            LIMIT 1
        """
        with get_connection() as conn, conn.cursor() as cur:
            cur.execute(sql, (enrollment_id, lesson_id))
            row = cur.fetchone()
            return row["score"] if row else None


class MySQLActivityRepository:
    def get_recency_and_streak(self, enrollment_id: str) -> tuple:
        sql = """
            SELECT
              DATEDIFF(CURDATE(), MAX(CASE WHEN achieved THEN achieved_date END)) AS recency_days,
              (SELECT COUNT(*) FROM (
                 SELECT achieved_date FROM daily_achievement
                 WHERE enrollment_id = %s AND achieved = 0
                 ORDER BY achieved_date DESC LIMIT 30
               ) recent_misses) AS miss_streak_days
            FROM daily_achievement
            WHERE enrollment_id = %s
        """
        with get_connection() as conn, conn.cursor() as cur:
            cur.execute(sql, (enrollment_id, enrollment_id))
            row = cur.fetchone()
            return (row["recency_days"] or 0, row["miss_streak_days"] or 0)


class MySQLRiskRepository:
    def save_risk_score(self, enrollment_id: str, score: float, label: str,
                         recency_days: int = None, miss_streak: int = None) -> None:
        sql = """
            INSERT INTO dropout_risk (enrollment_id, computed_at, risk_score, method, recency_days, miss_streak, features)
            VALUES (%s, NOW(), %s, 'RULE', %s, %s, JSON_OBJECT('label', %s))
        """
        with get_connection() as conn, conn.cursor() as cur:
            cur.execute(sql, (enrollment_id, score, recency_days, miss_streak, label))


class MySQLWeeklyProgressRepository:
    """야간 리플로우용 - Frozen Zone(locked=0 필터)을 쿼리 단에서 강제한다."""

    def get_cumulative_slip_minutes(self, enrollment_id: str) -> int:
        sql = """
            SELECT COALESCE(SUM(planned_min - actual_min), 0) AS slip
            FROM daily_achievement
            WHERE enrollment_id = %s AND achieved = 0
              AND achieved_date >= DATE_SUB(CURDATE(), INTERVAL 1 WEEK)
        """
        with get_connection() as conn, conn.cursor() as cur:
            cur.execute(sql, (enrollment_id,))
            return cur.fetchone()["slip"] or 0

    def get_weekly_average_minutes(self, enrollment_id: str) -> int:
        sql = "SELECT COALESCE(AVG(planned_min), 0) * 7 AS weekly_avg FROM daily_achievement WHERE enrollment_id = %s"
        with get_connection() as conn, conn.cursor() as cur:
            cur.execute(sql, (enrollment_id,))
            return int(cur.fetchone()["weekly_avg"] or 0)

    def get_remaining_lessons_this_week(self, enrollment_id: str) -> list[dict]:
        sql = """
            SELECT l.id, l.duration_seconds / 60 AS duration_min
            FROM schedule_slot ss
            JOIN weekly_schedule ws ON ws.id = ss.weekly_schedule_id
            JOIN lesson l ON l.id = ss.lesson_id
            WHERE ws.enrollment_id = %s AND ss.status = 'PLANNED'
              AND ws.locked = 0
              AND ss.plan_date >= CURDATE()
              AND YEARWEEK(ss.plan_date, 1) = YEARWEEK(CURDATE(), 1)
        """
        with get_connection() as conn, conn.cursor() as cur:
            cur.execute(sql, (enrollment_id,))
            return cur.fetchall()

    def get_remaining_days_this_week(self, enrollment_id: str) -> int:
        sql = "SELECT 7 - WEEKDAY(CURDATE()) - 1 AS remaining_days"
        with get_connection() as conn, conn.cursor() as cur:
            cur.execute(sql)
            return max(cur.fetchone()["remaining_days"], 0)

    def get_daily_cap_minutes(self, enrollment_id: str) -> int:
        # 다중코스 cap 정책: enrollment 단위가 아니라 student_capacity(학생=member 단위)
        sql = """
            SELECT sc.daily_cap_min
            FROM student_capacity sc
            JOIN enrollment e ON e.member_id = sc.student_id
            WHERE e.enrollment_id = %s
        """
        with get_connection() as conn, conn.cursor() as cur:
            cur.execute(sql, (enrollment_id,))
            row = cur.fetchone()
            return row["daily_cap_min"] if row and row["daily_cap_min"] else 60  # 폴백 기본값

    def save_day_assignment(self, enrollment_id: str, assignment: dict) -> None:
        sql = """
            UPDATE schedule_slot ss
            JOIN weekly_schedule ws ON ws.id = ss.weekly_schedule_id
            SET ss.plan_date = DATE_ADD(CURDATE(), INTERVAL %s + 1 DAY)
            WHERE ws.enrollment_id = %s AND ws.locked = 0 AND ss.lesson_id = %s
        """
        with get_connection() as conn, conn.cursor() as cur:
            for lesson_id, day_offset in assignment.items():
                cur.execute(sql, (day_offset, enrollment_id, lesson_id))
