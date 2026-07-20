"""application/ports.py의 인터페이스를 실제 RDS 쿼리로 구현.

2026-07-08: 종호(DBA)가 만든 V3.1.x/V3.3.x 마이그레이션(hc-backend develop 브랜치) 기준으로
전면 수정. 이 마이그레이션들은 아직 실제 RDS에 배포 전(develop→main 배포 대기 중)이라,
배포 전까지는 이 레포의 쿼리들이 대상 테이블 없음 에러를 낼 수 있음 - 정상.
컬럼명이 배포 후 다르면 이 파일만 고치면 되고 domain/application은 안 건드려도 됨.

2026-07-16 병합(schedule-optimization): 통합 CP-SAT + shadow mode 스케줄러가 추가로 필요로 하는
레포(StudentCap/CourseLearningPolicy/LessonProgress/StudentNotification/Experiment/Subscription/
WrongAnswer)를 종호의 실 스키마 정합본 위에 얹었다. 공유 레포(Lesson/Diagnostic/Schedule/
ReviewCard/QuizScore/Activity/Risk/WeeklyProgress)는 종호 실 스키마 버전을 유지한다.

핵심 스키마 메모:
- enrollment의 PK는 'id'가 아니라 'enrollment_id'
- lesson은 course_id를 직접 안 가짐 - lesson.section_id -> course_section.course_id로 조인
- quiz는 lesson이 아니라 course_id+section_id 단위 -> lesson_quiz_map(N:N)으로 강의와 연결
- daily_cap은 enrollment_onboarding이 아니라 student_capacity(student_id=member_id)에 있음(다중코스 cap 정책)
- 쉬는날 rest_days(비트마스크 bit0=일)는 enrollment_onboarding에 있음
- Frozen Zone: weekly_schedule.locked=1이면 리플로우 대상에서 반드시 제외

⚠️ 아직 실 스키마로 못 맞춘 레포(추정 스키마 유지, 배치 실행 전 검증 필요):
LessonProgress(lesson_progress/lecture) / StudentNotification(notification) / Experiment(experiment_*) /
Subscription(subscription.suneung_date - 실제 컬럼 없음, None 폴백). seed_demo는 이들을 우회하므로 데모엔 영향 없음.
"""
import json

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
    (수학은 느리고 영어는 빠른 학생 등 - 학생 전체 단일 계수로는 이런 편차를 못 잡음).
    ⚠️ 실 스키마는 member_lesson_stat(actual_completion_sec) - 배치 실행 전 정합 필요(seed_demo 우회 중)."""

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


class MySQLStudentNotificationRepository:
    """⚠️ 추정 스키마: notification 테이블에 학생용 배너를 적재 - 프론트가 조회해서 표시.
    error_router_client.py(내부 운영진 Slack 알림)와는 완전히 다른 채널이니 섞지 말 것.
    ⚠️ 실 스키마는 receiver_id/is_read/redirect_url이고 type ENUM에 SCHEDULE_* 없음 - 배치 실행 전 정합 필요."""

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


class MySQLPendingReviewRepository:
    """복습 갱신이 필요한 (enrollment_id, lesson_id) 목록. 컬럼은 실 마이그레이션 기준으로 확인함
    (enrollment.enrollment_id PK/member_id/status=V1, lesson_quiz_map=V3.1.1,
    quiz_submission.submitted_at=V3.3.1, review_card.last_review=V3.1.4).
    enrollment.status 는 ENUM('COMPLETED','ENROLLED','EXPIRED','IN_PROGRESS','REFUNDED')(V1) -
    '활성 수강'은 이 코드베이스 관례상 IN_PROGRESS(seed_demo 도 이 값으로 삽입, 다른 레포도 동일 필터 사용).

    조건: 활성 수강 + (카드 없음 OR 마지막 리뷰 이후 새 제출). 카드가 없으면 콜드스타트로 신규 생성되고,
    이미 최신 제출까지 반영된 카드는 대상에서 빠져 배치가 같은 카드를 매일 다시 굽지 않는다
    (당일 재리뷰로 처리되면 stability 가 왜곡되므로 이 멱등성이 중요하다).
    quiz 는 lesson 이 아니라 course+section 단위라 lesson_quiz_map(N:N)으로 강의와 잇는다.
    """

    def find_review_targets(self) -> list[tuple[str, str]]:
        sql = """
            SELECT DISTINCT e.enrollment_id, lqm.lesson_id
            FROM quiz_submission qs
            JOIN lesson_quiz_map lqm ON lqm.quiz_id = qs.quiz_id
            JOIN enrollment e ON e.member_id = qs.member_id
            LEFT JOIN review_card rc
              ON rc.enrollment_id = e.enrollment_id AND rc.lesson_id = lqm.lesson_id
            WHERE e.status = 'IN_PROGRESS'
              AND (rc.last_review IS NULL OR qs.submitted_at > rc.last_review)
        """
        with get_connection() as conn, conn.cursor() as cur:
            cur.execute(sql)
            return [(row["enrollment_id"], row["lesson_id"]) for row in cur.fetchall()]


class MySQLSubscriptionRepository:
    """⚠️ 추정 스키마: enrollment -> member -> subscription.suneung_date.
    실제 subscription엔 suneung_date 컬럼이 없어 배포 전까지 항상 None(상위 폴백 상수 사용) - 정합 필요."""

    def get_suneung_date(self, enrollment_id: str):
        sql = """
            SELECT s.suneung_date
            FROM enrollment e
            JOIN subscription s ON s.member_id = e.member_id
            WHERE e.enrollment_id = %s
        """
        with get_connection() as conn, conn.cursor() as cur:
            cur.execute(sql, (enrollment_id,))
            row = cur.fetchone()
            return row["suneung_date"] if row else None


class MySQLQuizScoreRepository:
    def get_latest_quiz_score(self, enrollment_id: str, lesson_id: str):
        # quiz는 lesson_id가 없어 lesson_quiz_map(N:N)으로 연결. quiz_submission.score는
        # 태연 쪽 스케일(0~100 가정, 확인 필요) - FSRS 임계값(90/70/50)과 스케일 안 맞으면 이 함수에서 변환.
        sql = """
            SELECT qs.score
            FROM quiz_submission qs
            JOIN lesson_quiz_map lqm ON lqm.quiz_id = qs.quiz_id
            JOIN enrollment e ON e.member_id = qs.member_id
            WHERE e.id = %s AND lqm.lesson_id = %s
            ORDER BY qs.submitted_at DESC
            LIMIT 1
        """
        with get_connection() as conn, conn.cursor() as cur:
            cur.execute(sql, (enrollment_id, lesson_id))
            row = cur.fetchone()
            return row["score"] if row else None

    def get_average_quiz_score(self, enrollment_id: str):
        # 전체 퀴즈 평균(%). 실 스키마 quiz_submission.score(0~100), enrollment→member 조인.
        # 응시 기록 없으면 None -> 규칙기반 risk는 2축으로 폴백.
        sql = """
            SELECT AVG(qs.score) AS avg_score
            FROM quiz_submission qs
            JOIN enrollment e ON e.member_id = qs.member_id
            WHERE e.enrollment_id = %s
        """
        with get_connection() as conn, conn.cursor() as cur:
            cur.execute(sql, (enrollment_id,))
            row = cur.fetchone()
            return float(row["avg_score"]) if row and row["avg_score"] is not None else None


class MySQLWrongAnswerRepository:
    """학생의 특정 퀴즈 '최신 제출'에서 틀린 question_id 목록.

    quiz_submission(제출 헤더) -> quiz_submission_answer(문항별 정오) 조인.
    같은 학생이 같은 퀴즈를 여러 번 냈으면 submitted_at이 가장 최신인 제출만 본다(재응시 이전 오답 배제).
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


class MySQLEnrollmentQuizResolver:
    """FSRS 파이프라인 키(enrollment_id, lesson_id) -> 추천 유스케이스 키(member_id, quiz_id) 변환.
    종호 배치는 자기가 가진 enrollment_id/lesson_id만 넘기면 되고 매핑은 여기서 처리한다.
    lesson↔quiz는 N:N(lesson_quiz_map)이라 '이 학생이 그 레슨 퀴즈 중 가장 최근 제출한 quiz'를 택함."""

    def get_member_id(self, enrollment_id: str):
        sql = "SELECT member_id FROM enrollment WHERE id = %s"
        with get_connection() as conn, conn.cursor() as cur:
            cur.execute(sql, (enrollment_id,))
            row = cur.fetchone()
            return row["member_id"] if row else None

    def get_latest_quiz_id(self, member_id: str, lesson_id: str):
        sql = """
            SELECT qs.quiz_id
            FROM quiz_submission qs
            JOIN lesson_quiz_map lqm ON lqm.quiz_id = qs.quiz_id
            WHERE qs.member_id = %s AND lqm.lesson_id = %s
            ORDER BY qs.submitted_at DESC
            LIMIT 1
        """
        with get_connection() as conn, conn.cursor() as cur:
            cur.execute(sql, (member_id, lesson_id))
            row = cur.fetchone()
            return row["quiz_id"] if row else None


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
        # method는 실 스키마 ENUM 대문자('RULE'/'COX').
        sql = """
            INSERT INTO dropout_risk (enrollment_id, computed_at, risk_score, method, features)
            VALUES (%s, NOW(), %s, 'RULE',
                    JSON_OBJECT('label', %s,
                                'top_reason', %s,
                                'contributions', CAST(%s AS JSON)))
        """
        with get_connection() as conn, conn.cursor() as cur:
            cur.execute(sql, (enrollment_id, score, label, top_reason, json.dumps(contributions)))


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
