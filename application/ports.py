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


class NotifierPort(Protocol):
    def notify_failure(self, title: str, message: str) -> None:
        """실패 시 error-router(/webhook/error)로 Slack 알림"""
        ...
