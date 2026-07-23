"""Repository/외부연동 포트(인터페이스). infrastructure가 이걸 구현한다.

자바의 인터페이스와 동일한 역할 - Protocol이라 상속 없이도 구조적으로 만족하면 됨.
application/domain은 이 Protocol만 알고, 구체 구현(MySQL 등)은 모른다.
"""
from datetime import date
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

    def get_average_quiz_score(self, enrollment_id: str) -> Optional[float]:
        """전체 퀴즈 평균 점수(%). 응시 기록 없으면 None -> 규칙기반 risk는 2축으로 폴백."""
        ...


class PendingReviewRepository(Protocol):
    def find_review_targets(self) -> list[tuple[str, str]]:
        """FSRS 복습 갱신이 필요한 (enrollment_id, lesson_id) 목록.

        새 퀴즈 제출이 그 카드의 마지막 리뷰 이후에 생겼거나(=점수 갱신됨) 카드가 아직 없는
        활성 수강 건. 야간/주간 배치가 이 목록만큼 ReviewLessonUseCase 를 돌린다.
        """
        ...


class SubscriptionRepository(Protocol):
    def get_suneung_date(self, enrollment_id: str) -> Optional[date]:
        """구독 상한선(수능일). 구독에 안 잡혀있으면 None -> 호출부가 기본 상한값으로 대체."""
        ...


class StudentCapRepository(Protocol):
    def get_weekly_available_minutes(self, member_id: str) -> int:
        """학생레벨 총 주간 가용시간(분) - 온보딩(하루cap·쉬는날) 기준.
        온보딩 미완료면 콜드스타트 기본값(관리자 전역정책값 후보) 반환."""
        ...

    def get_study_days(self, member_id: str) -> int:
        """주당 학습일수(7 - 쉬는날 수). 코스별 '하루 최대 학습 시간'을 주간 상한으로
        환산할 때 쓴다(daily_max × study_days). 온보딩 미완료면 기본값."""
        ...


class CourseLearningPolicyRepository(Protocol):
    def get_daily_max_minutes(self, course_ids: list[str]) -> dict[str, int]:
        """{course_id: 하루 최대 학습 분} - 강사가 코스 등록 시 정한 코스별 강도 상한
        (course_learning_policy.daily_recommended_minutes). 값이 없는 코스는 키에서 빠짐(=상한 없음)."""
        ...


class LessonProgressRepository(Protocol):
    def get_completed_lesson_durations(self, member_id: str) -> list[dict]:
        """[{"course_id","expected_duration_min","actual_duration_min"}] - 완료된 강의의
        강사 추정치 대 실제 소요시간. course_id별로 묶어서 compute_efficiency_coefficient()에
        넣으면 코스별 효율계수가 나옴(과목마다 학습속도가 다를 수 있어 학생 전체 단일값 대신 분리)."""
        ...


class ActivityRepository(Protocol):
    def get_recency_and_streak(self, enrollment_id: str) -> tuple[int, int]:
        """(마지막활동후경과일, 연속미달성일수) 반환"""
        ...


class RiskRepository(Protocol):
    def save_risk_score(
        self,
        enrollment_id: str,
        score: float,
        label: str,
        contributions: dict[str, float],
        top_reason: str,
    ) -> None:
        """contributions: 축별 기여도(합=score), top_reason: 최대 기여 축 코드.
        dropout_risk.features(JSON)에 함께 저장 - 대시보드/상세가 이 값을 읽는다."""
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


class StudentNotificationRepository(Protocol):
    """학생이 앱에서 직접 보는 배너 알림 - NotifierPort(내부 운영진 Slack 알림)와는 다른 채널."""

    def notify_schedule_extended(self, member_id: str, extended_weeks: int) -> None:
        """물리적으로 불가능해서 목표기간을 extended_weeks만큼 늘려 재조정했음을 알림."""
        ...

    def notify_schedule_infeasible(self, member_id: str) -> None:
        """확장 재시도까지 실패 - 학생이 직접 목표기간/학습량을 조정해야 함을 알림."""
        ...


class ExperimentRepository(Protocol):
    def log_exposure(self, member_id: str, experiment_name: str, variant) -> None:
        """이 학생이 이번 배치에서 어떤 실험 variant를 적용받았는지 기록.
        나중에 실제 성과(완주율·성적 향상)와 조인해서 scripts/calibrate_policy_constants.py의
        A/B 분석에 쓴다 - 배정 자체는 domain/experiments.py가 결정적으로 계산하므로
        이 포트는 "기록"만 담당(배정 로직 자체는 안 들고 있음)."""
        ...

    def log_shadow_decision(self, member_id: str, experiment_name: str, decision: dict) -> None:
        """Shadow mode: 배정 variant를 '실제로 적용했다면' 결정이 얼마나 달라졌을지를 real
        traffic에서 관측만 해 기록. 사용자 스케줄엔 반영 안 함(application은 baseline만 저장).
        decision: coeff 요약, extension_delta, weekly_minutes_delta, schedule_would_change 등.
        성과(점수 향상) 증거가 아니라 '정책 변화량·과부하 가능성·결정 뒤집힘'을 실측으로 보기 위함."""
        ...

    def get_shadow_decisions(self, experiment_name: str) -> list[dict]:
        """log_shadow_decision으로 쌓인 결정들을 관리자 집계용으로 조회.
        각 원소는 기록 당시의 decision dict와 같은 형태(domain/shadow_report.py가 이걸 요약)."""
        ...
