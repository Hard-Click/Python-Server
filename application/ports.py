"""Repository/외부연동 포트(인터페이스). infrastructure가 이걸 구현한다.

자바의 인터페이스와 동일한 역할 - Protocol이라 상속 없이도 구조적으로 만족하면 됨.
application/domain은 이 Protocol만 알고, 구체 구현(MySQL 등)은 모른다.
"""
from typing import Protocol, Optional
from domain.review import Card


class LessonRepository(Protocol):
    def get_lessons_for_course(self, course_id: str) -> list[dict]:
        """[{"id","duration_min","deadline_week"}] 반환"""
        ...

    def get_prerequisites(self, course_id: str) -> list[tuple[str, str]]:
        ...


class DiagnosticScoreRepository(Protocol):
    def get_grades_for_student(self, member_id: str, course_ids: list[str]) -> dict:
        """{course_id: 등급(1~9)} 반환. 모의고사 안 본 과목은 키에서 빠짐."""
        ...


class ScheduleRepository(Protocol):
    def save_weekly_schedule(self, enrollment_id: str, week_no: int, assignment: dict) -> None:
        ...


class ReviewCardRepository(Protocol):
    def get_card(self, enrollment_id: str, lesson_id: str) -> Optional[Card]:
        ...

    def save_card(self, enrollment_id: str, lesson_id: str, card: Card) -> None:
        ...


class QuizScoreRepository(Protocol):
    def get_latest_quiz_score(self, enrollment_id: str, lesson_id: str) -> Optional[float]:
        ...


class ActivityRepository(Protocol):
    def get_recency_and_streak(self, enrollment_id: str) -> tuple[int, int]:
        """(마지막활동후경과일, 연속미달성일수) 반환"""
        ...


class RiskRepository(Protocol):
    def save_risk_score(self, enrollment_id: str, score: float, label: str) -> None:
        ...


class WeeklyProgressRepository(Protocol):
    """야간 미세조정(nightly_reflow)에 필요한 조회 - Frozen Zone 지키려고 '남은 날짜'만 다룸."""

    def get_cumulative_slip_minutes(self, enrollment_id: str) -> int:
        ...

    def get_weekly_average_minutes(self, enrollment_id: str) -> int:
        """콜드스타트(데이터 없음)면 0 반환 -> compute_slip_status가 on_track 처리"""
        ...

    def get_remaining_lessons_this_week(self, enrollment_id: str) -> list[dict]:
        """[{"id","duration_min"}] - 이번 주 아직 안 끝낸 것만"""
        ...

    def get_remaining_days_this_week(self, enrollment_id: str) -> int:
        """오늘 이후 이번 주 남은 날 수"""
        ...

    def get_daily_cap_minutes(self, enrollment_id: str) -> int:
        ...

    def save_day_assignment(self, enrollment_id: str, assignment: dict) -> None:
        """{lesson_id: day_offset} 저장 - 이번 주 스케줄 슬롯 갱신"""
        ...


class NotifierPort(Protocol):
    def notify_failure(self, title: str, message: str) -> None:
        """실패 시 error-router(/webhook/error)로 Slack 알림"""
        ...
