"""유스케이스 - domain 로직과 repository(포트)를 엮어서 실제 흐름을 수행.

DB가 뭔지(MySQL인지), 어떻게 조회하는지는 여기서 전혀 모른다 - 포트만 호출한다.
"""
from datetime import date

from domain.scheduler import (
    generate_unified_weekly_schedule, compute_num_weeks, compute_efficiency_coefficient,
    compute_required_extension_weeks, SLIP_BUFFER_WEEKS,
)
from domain.review import review_lesson
from domain.risk import compute_risk_breakdown
from domain.reflow import compute_slip_status, redistribute_remaining_week
from domain.experiments import assign_variant
from domain.shadow_report import summarize_shadow_decisions
from domain.stretch_guardrails import evaluate_guardrails
from domain.schedule_quality import evaluate_schedule
from application.ports import (
    LessonRepository, DiagnosticScoreRepository, ScheduleRepository,
    ReviewCardRepository, QuizScoreRepository, ActivityRepository, RiskRepository,
    WeeklyProgressRepository, SubscriptionRepository, LessonProgressRepository,
    StudentNotificationRepository, ExperimentRepository,
    WrongAnswerRepository, ProblemRecommenderPort, CourseLearningPolicyRepository,
)

# 구독에 수능일이 아직 안 잡혀있는 경우(온보딩 미완료 등)의 폴백 상한 - 관리자 전역정책값
# 후보(현재는 상수, weekly_reflow.py의 다른 TODO 플레이스홀더와 동일 성격).
DEFAULT_MAX_REVIEW_INTERVAL_DAYS = 180

# EFFICIENCY_STRETCH_FACTOR는 검증 안 된 정책값(docs/policy_constants.md) - 관찰 데이터로는
# 인과관계를 못 밝혀서 A/B 실험이 필요함(확정안, 2026-07-09). 학생마다 결정적으로 후보값 중
# 하나를 배정해서 나중에 완주율/성적 향상과 조인해 비교(scripts/calibrate_policy_constants.py).
EFFICIENCY_STRETCH_EXPERIMENT_NAME = "efficiency_stretch_factor"
EFFICIENCY_STRETCH_VARIANTS = [0.3, 0.5, 0.7]

# --- Shadow mode (docs/roadmap_stretch_factor_offline.md, 2026-07) ---
# Phase 2 결론: 어떤 고정 stretch_factor도 오프라인만으로는 정당화 불가 - 실사용자 없이는
# "관측된 느림이 진짜냐"(잠재변수)를 못 가르기 때문. 그래서 실측이 쌓이기 전까지는 배정된
# variant를 실제 스케줄에 '적용'하지 않고, 강사 추정치 그대로(sf=0.0, 기능 off)로만 스케줄을
# 만든다 - 유발 infeasibility(유일한 하드 실패)가 0이고 되돌리기 쉬운 가장 보수적 운영값.
# 배정 variant는 shadow로 계산만 해서 "실제로 적용했다면 결정이 얼마나 달라졌을지"를 real
# traffic 로그로 남긴다(성과 증거 아님 - 정책 변화량·과부하 가능성·결정 뒤집힘 관측용).
# 실측이 쌓이면 이 플래그를 끄고(또는 opt-in 베타로) sf=f(signal) 개인화(Phase 3)로 간다.
SHADOW_MODE = True
SHADOW_APPLIED_STRETCH_FACTOR = 0.0

# 통합 CP-SAT이 INFEASIBLE이면 부족분을 역산해 필요한 만큼 주차를 늘려 1회 재시도한다
# (확정안, 2026-07-09) - 고정폭이 아니라 compute_required_extension_weeks()로 계산한 값을 씀.
# SLIP_BUFFER_WEEKS(하드 상한 슬립 버퍼, 2주)를 넘는 확장은 안 하고 - 그 안에서도 안 풀리면
# 재시도 없이 바로 학생에게 "물리적으로 불가능" 알림. 수능일 자체도 절대 못 넘도록 클램핑.
MAX_INFEASIBLE_EXTENSION_WEEKS = SLIP_BUFFER_WEEKS


class GenerateWeeklyScheduleUseCase:
    """학생 1명의 활성 코스 전체를 대상으로 주간 스케줄 생성.

    코스별로 등급 비율로 주간예산을 먼저 쪼개고 각자 독립적으로 CP-SAT을 푸는 방식은
    "코스마다 마감까지 남은 주차수(num_weeks)가 다르다"를 무시해서, 등급이 같아도
    마감이 급한 코스가 INFEASIBLE로 터지는 문제가 있었다. 대신 전체 코스의 강의를
    하나의 CP-SAT 모델에 넣어 학생의 실제 공유 주간시간 안에서 공동 최적화한다 -
    마감 압박은 강의별 deadline_week 제약으로, 등급은 목적함수(소프트 선호)로 반영.

    duration_min은 강사가 잡은 고정 추정치를 코스별 효율계수(그 코스에서 실제소요시간/추정치)로
    스케일링해서 씀 - 안 그러면 target_weeks·cap·선수관계는 개인 입력값이라 day1부터
    정확해도, "강의가 몇 분 걸리느냐"는 계속 강사 추정치 그대로라 실제로는 안 맞음.
    코스별로 분리한 이유: 같은 학생도 수학은 느리고 영어는 빠를 수 있어서 - 학생 전체
    단일 계수를 쓰면 이런 과목간 편차를 못 잡고 서로 오염시킴.

    통합모델이라 코스 하나만 물리적으로 불가능해도 전체가 INFEASIBLE로 나올 수 있다
    (확정안, 2026-07-09): 이 경우 MAX_INFEASIBLE_EXTENSION_WEEKS만큼 주차를 늘려 1회
    재시도하고, 그래도 안 되면 학생에게 "물리적으로 불가능" 배너를 띄운다. 성공한 경우도
    확장이 있었으면 "목표기간이 늘어났다"는 배너를 띄운다(조용히 뒤로 미루지 않음).
    """

    def __init__(
        self,
        lesson_repo: LessonRepository,
        diagnostic_repo: DiagnosticScoreRepository,
        schedule_repo: ScheduleRepository,
        subscription_repo: SubscriptionRepository,
        lesson_progress_repo: LessonProgressRepository,
        notification_repo: StudentNotificationRepository,
        experiment_repo: ExperimentRepository,
        clock=date.today,
        course_policy_repo: CourseLearningPolicyRepository | None = None,
    ):
        self.lesson_repo = lesson_repo
        self.diagnostic_repo = diagnostic_repo
        self.schedule_repo = schedule_repo
        self.subscription_repo = subscription_repo
        self.lesson_progress_repo = lesson_progress_repo
        self.notification_repo = notification_repo
        self.experiment_repo = experiment_repo
        self.clock = clock  # 날짜 의존 로직 주입점(테스트에서 고정 날짜 주입 - date.today 기본)
        # 코스별 강도 상한 조회(선택 주입). 없으면 코스별 주간 상한 미적용(하위호환) - 강사가
        # 코스 등록 시 정한 daily_recommended_minutes를 CP-SAT에 실제로 물리는 경로.
        self.course_policy_repo = course_policy_repo

    def execute(self, member_id: str, enrollments: list[dict], total_weekly_minutes: int, commit: bool = True, study_days: int | None = None):
        """enrollments: [{"enrollment_id","course_id","enrolled_at","target_weeks"}]
        target_weeks는 nullable(온보딩 미완료 시 None -> 수능일-버퍼까지 전부 사용).

        study_days: 주당 학습일수(7-쉬는날). 코스별 '하루 최대 학습 시간'을 주간 상한으로 환산할 때
        쓴다. None이거나 course_policy_repo 미주입이면 코스별 주간 상한을 걸지 않는다(하위호환).

        commit=True(기본, 실제 배치/온보딩 확정): 스케줄 저장·알림·실험(exposure/shadow) 로그까지 수행.
        commit=False(미리보기): CP-SAT 계산만 하고 **부작용(저장/알림/로그) 전부 생략** - preview가
        DB·알림·실험 데이터를 오염시키지 않게 한다."""
        course_ids = [e["course_id"] for e in enrollments]
        grades = self.diagnostic_repo.get_grades_for_student(member_id, course_ids)
        course_weekly_caps = self._compute_course_weekly_caps(course_ids, study_days)

        variant_stretch_factor = assign_variant(member_id, EFFICIENCY_STRETCH_EXPERIMENT_NAME, EFFICIENCY_STRETCH_VARIANTS)
        if commit:
            self.experiment_repo.log_exposure(member_id, EFFICIENCY_STRETCH_EXPERIMENT_NAME, variant_stretch_factor)

        # Shadow mode: 실제 스케줄엔 배정 variant를 적용하지 않고 강사 추정치 그대로(sf=0.0)로 만든다.
        applied_stretch_factor = SHADOW_APPLIED_STRETCH_FACTOR if SHADOW_MODE else variant_stretch_factor

        completed_by_course = {}
        for row in self.lesson_progress_repo.get_completed_lesson_durations(member_id):
            completed_by_course.setdefault(row["course_id"], []).append(row)
        applied_efficiency_by_course = {
            course_id: compute_efficiency_coefficient(completed_by_course.get(course_id, []), applied_stretch_factor)
            for course_id in course_ids
        }

        applied = self._solve_with_extension(enrollments, applied_efficiency_by_course, grades, total_weekly_minutes, course_weekly_caps)

        if applied["assignment"] is None:
            if commit:
                self.notification_repo.notify_schedule_infeasible(member_id)
                if SHADOW_MODE:
                    self._log_shadow_decision(
                        member_id, variant_stretch_factor, applied_stretch_factor, applied_efficiency_by_course,
                        completed_by_course, course_ids, enrollments, grades, total_weekly_minutes, applied, False,
                        course_weekly_caps,
                    )
            return {e["enrollment_id"]: {"status": "INFEASIBLE"} for e in enrollments}

        if commit and applied["extension_weeks"] > 0:
            self.notification_repo.notify_schedule_extended(member_id, applied["extension_weeks"])

        by_enrollment = {}
        for lesson_id, week in applied["assignment"].items():
            by_enrollment.setdefault(applied["lesson_to_enrollment"][lesson_id], {})[lesson_id] = week

        results = {}
        for enrollment in enrollments:
            enrollment_id = enrollment["enrollment_id"]
            enrollment_assignment = by_enrollment.get(enrollment_id, {})
            if commit:
                self.schedule_repo.save_weekly_schedule(enrollment_id, 0, enrollment_assignment)
            results[enrollment_id] = {"status": "OK", "assignment": enrollment_assignment}

        # Shadow 결정 로그(applied 확정 후): 배정 variant를 '적용했다면' 결정이 얼마나 달랐을지 관측만.
        # 사용자 스케줄엔 영향 없음. commit=False(preview)면 shadow 로그도 안 남긴다(실배치 이벤트만 기록).
        if commit and SHADOW_MODE:
            self._log_shadow_decision(
                member_id, variant_stretch_factor, applied_stretch_factor, applied_efficiency_by_course,
                completed_by_course, course_ids, enrollments, grades, total_weekly_minutes, applied, True,
                course_weekly_caps,
            )
        return results

    def _compute_course_weekly_caps(self, course_ids: list[str], study_days: int | None) -> dict[str, int]:
        """코스별 '하루 최대 학습 분'(course_learning_policy.daily_recommended_minutes)을 주간
        상한으로 환산: daily_max × 학습일수. 이 상한이 있어야 강사가 코스 등록 때 정한 '코스별
        강도 상한'이 실제 CP-SAT 스케줄에 반영된다(그 전엔 저장만 되고 스케줄러가 무시했음).
        course_policy_repo 미주입이거나 study_days가 없으면 빈 dict(=상한 미적용, 하위호환)."""
        if self.course_policy_repo is None or not study_days:
            return {}
        daily_max = self.course_policy_repo.get_daily_max_minutes(course_ids)
        return {course_id: minutes * study_days for course_id, minutes in daily_max.items()}

    def _solve_with_extension(self, enrollments, efficiency_by_course, grades, total_weekly_minutes, course_weekly_caps=None):
        """CP-SAT solve + INFEASIBLE 시 연장 1회 재시도. applied/shadow가 공유(중복 제거, shadow도
        동일 로직으로 실제 feasibility를 얻어 코스 경쟁까지 반영). 반환: assignment(None=완주 불가),
        extension_weeks, lesson_to_enrollment, total_min(ext=0 총 분량), base_weeks(ext=0 주수).
        course_weekly_caps: 코스별 주간 상한(분) - CP-SAT 하드 제약으로 전달(applied/shadow 동일)."""
        all_lessons, all_prerequisites, lesson_to_enrollment, max_num_weeks, course_deadline_weeks = (
            self._build_model_inputs(enrollments, efficiency_by_course, extension_weeks=0)
        )
        base_weeks = max_num_weeks
        total_min = sum(lesson["duration_min"] for lesson in all_lessons)
        weekly_caps = [total_weekly_minutes] * max_num_weeks
        assignment = generate_unified_weekly_schedule(all_lessons, weekly_caps, all_prerequisites, grade_weights=grades, course_weekly_caps=course_weekly_caps)

        extension_weeks = 0
        if assignment is None:
            course_totals = self._course_totals(all_lessons, course_deadline_weeks)
            extension_weeks = compute_required_extension_weeks(course_totals, total_weekly_minutes, MAX_INFEASIBLE_EXTENSION_WEEKS)
            if extension_weeks > 0:
                all_lessons, all_prerequisites, lesson_to_enrollment, max_num_weeks, _ = (
                    self._build_model_inputs(enrollments, efficiency_by_course, extension_weeks)
                )
                weekly_caps = [total_weekly_minutes] * max_num_weeks
                assignment = generate_unified_weekly_schedule(all_lessons, weekly_caps, all_prerequisites, grade_weights=grades, course_weekly_caps=course_weekly_caps)
        return {
            "assignment": assignment, "extension_weeks": extension_weeks,
            "lesson_to_enrollment": lesson_to_enrollment, "total_min": total_min, "base_weeks": base_weeks,
            # 품질 지표(domain/schedule_quality.py)용 - 연장 재시도 후 최종 lessons/주수(assignment와 짝이 맞아야 함).
            "lessons": all_lessons, "num_weeks": max_num_weeks,
        }

    def _build_model_inputs(self, enrollments, efficiency_by_course, extension_weeks):
        all_lessons = []
        all_prerequisites = []
        lesson_to_enrollment = {}
        course_deadline_weeks = {}
        max_num_weeks = 1

        for enrollment in enrollments:
            course_id = enrollment["course_id"]
            enrollment_id = enrollment["enrollment_id"]
            suneung_date = self.subscription_repo.get_suneung_date(enrollment_id)
            num_weeks = compute_num_weeks(
                today=self.clock(),
                enrolled_at=enrollment["enrolled_at"],
                target_weeks=enrollment.get("target_weeks"),
                suneung_date=suneung_date,
            )
            if extension_weeks:
                num_weeks += extension_weeks
                if suneung_date is not None:
                    # 확장해도 수능일 자체는 절대 못 넘김(절대 상한) - 리뷰버퍼까지만 잠식 가능.
                    absolute_ceiling_weeks = max(1, (suneung_date - self.clock()).days // 7)
                    num_weeks = min(num_weeks, absolute_ceiling_weeks)

            max_num_weeks = max(max_num_weeks, num_weeks)
            course_deadline_week = num_weeks - 1
            course_deadline_weeks[course_id] = course_deadline_week

            for lesson in self.lesson_repo.get_lessons_for_course(course_id):
                existing_deadline = lesson.get("deadline_week")
                deadline_week = (
                    min(existing_deadline, course_deadline_week)
                    if existing_deadline is not None
                    else course_deadline_week
                )
                # 최소 1분 보장: 짧은 강의 × 작은 계수가 round로 0분이 되면 CP-SAT 제약에 나쁜 신호
                # (0분 강의는 "시간 안 드는 일"로 취급돼 스케줄 품질을 왜곡). 원래 양수면 최소 1분.
                raw_duration = lesson["duration_min"] * efficiency_by_course[course_id]
                adjusted_duration = max(1, round(raw_duration)) if lesson["duration_min"] > 0 else 0
                all_lessons.append({
                    **lesson,
                    "course_id": course_id,
                    "deadline_week": deadline_week,
                    "duration_min": adjusted_duration,
                })
                lesson_to_enrollment[lesson["id"]] = enrollment_id

            all_prerequisites.extend(self.lesson_repo.get_prerequisites(course_id))

        return all_lessons, all_prerequisites, lesson_to_enrollment, max_num_weeks, course_deadline_weeks

    @staticmethod
    def _course_totals(all_lessons, course_deadline_weeks):
        """compute_required_extension_weeks()에 넘길 코스별 남은 총 분량 - 이미 효율계수가
        적용된 duration_min 합계와 그 코스의(확장 전) 마감 주차를 묶는다."""
        totals = {}
        for lesson in all_lessons:
            totals[lesson["course_id"]] = totals.get(lesson["course_id"], 0) + lesson["duration_min"]
        return [
            {"total_duration_min": total, "deadline_week": course_deadline_weeks[course_id]}
            for course_id, total in totals.items()
        ]

    def _log_shadow_decision(
        self, member_id, variant_sf, applied_sf, applied_efficiency, completed_by_course,
        course_ids, enrollments, grades, total_weekly_minutes, applied_result, applied_feasible,
        course_weekly_caps=None,
    ):
        """배정 variant를 '적용했다면'의 결정을 applied(=baseline)와 비교해 로깅 + 가드레일 평가.
        infeasibility는 근사가 아니라 **실제 shadow CP-SAT**로 판정(코스 경쟁 반영) - 그래서 shadow도
        _solve_with_extension으로 실제 풀어본다(학생당 CP-SAT 2회, 주간 배치라 수용). 성과 증거 아님.
        course_weekly_caps는 applied와 동일하게 걸어야 순수 stretch_factor 효과만 비교된다."""
        if not course_ids:
            return
        shadow_efficiency = {
            c: compute_efficiency_coefficient(completed_by_course.get(c, []), variant_sf)
            for c in course_ids
        }
        shadow_result = self._solve_with_extension(enrollments, shadow_efficiency, grades, total_weekly_minutes, course_weekly_caps)
        shadow_feasible = shadow_result["assignment"] is not None

        base_weeks = max(1, applied_result["base_weeks"])
        applied_weekly = applied_result["total_min"] / base_weeks
        shadow_weekly = shadow_result["total_min"] / base_weeks   # 같은 horizon 대비 = 순수 부하 효과
        weekly_delta = shadow_weekly - applied_weekly
        weekly_delta_pct = 100 * weekly_delta / applied_weekly if applied_weekly >= 1.0 else 0.0
        completed_counts = [len(completed_by_course.get(c, [])) for c in course_ids]

        # 가드레일: 이 variant를 '적용했다면' 사고(치명/연장/부하급증)를 쳤을지, 신호가 없는지 평가.
        # shadow에선 관측만(적용은 baseline). variant 실적용(opt-in beta) 시엔 action=fallback_baseline이
        # 실제 sf를 막는다. sf 최적화가 아니라 사고 방지.
        guardrail = evaluate_guardrails(
            applied_feasible, shadow_feasible,
            applied_result["extension_weeks"], shadow_result["extension_weeks"],
            completed_counts, weekly_delta_pct, weekly_delta,
        )

        # 스케줄 품질 비교(domain/schedule_quality.py, 순수): "variant가 부하 분산을 개선/악화시키나".
        # 둘 다 완주 가능할 때만 의미 있다 - infeasible이면 품질 비교 이전에 이미 가드레일이
        # 하드 실패로 잡는다(치명 vs 편중도를 같은 저울에 놓으면 안 됨).
        quality_delta = None
        if applied_feasible and shadow_feasible:
            applied_quality = evaluate_schedule(
                applied_result["assignment"], applied_result["lessons"], total_weekly_minutes, applied_result["num_weeks"],
            )
            shadow_quality = evaluate_schedule(
                shadow_result["assignment"], shadow_result["lessons"], total_weekly_minutes, shadow_result["num_weeks"],
            )
            quality_delta = {
                "load_cv_delta": round(shadow_quality["load_cv"] - applied_quality["load_cv"], 3),
                "overloaded_weeks_delta": shadow_quality["overloaded_weeks"] - applied_quality["overloaded_weeks"],
                "max_consecutive_overload_delta": (
                    shadow_quality["max_consecutive_overload_weeks"] - applied_quality["max_consecutive_overload_weeks"]
                ),
                "peak_subject_share_delta": round(
                    shadow_quality["peak_subject_share"] - applied_quality["peak_subject_share"], 3
                ),
            }

        payload = {
            "variant": variant_sf,
            "applied_stretch_factor": applied_sf,
            "applied_coeff_mean": round(sum(applied_efficiency.values()) / len(applied_efficiency), 4),
            "shadow_coeff_mean": round(sum(shadow_efficiency.values()) / len(shadow_efficiency), 4),
            "applied_total_min": round(applied_result["total_min"]),
            "shadow_total_min": round(shadow_result["total_min"]),
            "applied_feasible": applied_feasible,
            "shadow_feasible": shadow_feasible,
            "extension_delta": shadow_result["extension_weeks"] - applied_result["extension_weeks"],
            "weekly_minutes_delta": round(weekly_delta, 1),
            "schedule_would_change": (applied_feasible != shadow_feasible)
                or (shadow_result["extension_weeks"] != applied_result["extension_weeks"])
                or (abs(weekly_delta) > 0.05 * max(1.0, applied_weekly)),
            "guardrail_triggered": guardrail["triggered"],
            "guardrails": guardrail["fired"],
            "guardrails_hard": guardrail["hard"],
            "guardrails_soft": guardrail["soft"],
            "would_have_failed_without_guardrail": guardrail["would_have_failed"],
            "guardrail_action": guardrail["action"],
            "guardrail_numbers": guardrail["numbers"],
            "quality_delta": quality_delta,  # None이면 둘 중 하나 infeasible(품질 비교 대상 아님)
        }
        self.experiment_repo.log_shadow_decision(member_id, EFFICIENCY_STRETCH_EXPERIMENT_NAME, payload)


class SummarizeShadowDecisionsUseCase:
    """관리자용: shadow mode 로그를 조회해 '배정 variant를 적용했다면 결정이 얼마나 달라졌을지'
    요약. 성과 증거가 아니라 정책 변화량 관측용. 조회는 포트, 집계는 domain/shadow_report.py."""

    def __init__(self, experiment_repo: ExperimentRepository):
        self.experiment_repo = experiment_repo

    def execute(self, experiment_name: str = EFFICIENCY_STRETCH_EXPERIMENT_NAME) -> dict:
        decisions = self.experiment_repo.get_shadow_decisions(experiment_name)
        return summarize_shadow_decisions(decisions)


class ReviewLessonUseCase:
    def __init__(
        self,
        card_repo: ReviewCardRepository,
        quiz_repo: QuizScoreRepository,
        subscription_repo: SubscriptionRepository,
        clock=date.today,
    ):
        self.card_repo = card_repo
        self.quiz_repo = quiz_repo
        self.subscription_repo = subscription_repo
        self.clock = clock

    def execute(self, enrollment_id: str, lesson_id: str):
        score = self.quiz_repo.get_latest_quiz_score(enrollment_id, lesson_id)
        if score is None:
            return None  # 퀴즈 미응시 - 복습 스케줄링 대상 아님
        card = self.card_repo.get_card(enrollment_id, lesson_id)
        max_interval_days = self._max_interval_days(enrollment_id)
        new_card, due = review_lesson(card, score, max_interval_days=max_interval_days)
        self.card_repo.save_card(enrollment_id, lesson_id, new_card)
        return due

    def _max_interval_days(self, enrollment_id: str) -> int:
        suneung_date = self.subscription_repo.get_suneung_date(enrollment_id)
        if suneung_date is None:
            return DEFAULT_MAX_REVIEW_INTERVAL_DAYS
        return max(1, (suneung_date - self.clock()).days)


class RecommendSimilarProblemsUseCase:
    """학생이 퀴즈에서 틀린 문제마다 유사문제를 추천(BE quiz_recommender 계약을 해석).

    리뷰(ReviewLessonUseCase)와 분리한 이유: 리뷰는 야간/주간 배치에서 카드 상태를 갱신하는
    관심사고, 유사문제 추천은 학생이 퀴즈를 낸 직후 요청 시점에 "틀린 것 비슷한 문제 더 풀어봐"를
    돌려주는 관심사라 트리거·주체가 다르다.

    계약 해석(단위테스트로 고정):
      - []              = 잘못된/없는 problem_id  -> 그 문제는 건너뜀
      - [원문제]          = 유사문제 0개(RDS/indexer 미가동 시 포함) -> 추천 없음, 건너뜀
      - [원문제, 유사...]  = result[0]=원문제(자기 자신, 버림), result[1:]=유사문제(추천)
    반환: {틀린 question_id: [유사 question_id, ...]} - 추천이 실제로 있는 문제만 담는다.
    """

    def __init__(self, wrong_answer_repo: WrongAnswerRepository, recommender: ProblemRecommenderPort):
        self.wrong_answer_repo = wrong_answer_repo
        self.recommender = recommender

    def execute(self, student_id: int, quiz_id: int, k: int = 2) -> dict[int, list[int]]:
        wrong_qids = self.wrong_answer_repo.get_wrong_question_ids(student_id, quiz_id)
        recommendations = {}
        for qid in wrong_qids:
            result = self.recommender.get_similar_problems(student_id, qid, k)
            if not result:
                continue  # [] = 잘못된 id -> 건너뜀(BE 계약)
            similars = result[1:]  # result[0]은 원문제(자기 자신)
            if similars:  # [원문제]만 온 경우(유사 0개)는 추천 없음으로 스킵
                recommendations[qid] = similars
        return recommendations


class ComputeRiskUseCase:
    def __init__(
        self,
        activity_repo: ActivityRepository,
        risk_repo: RiskRepository,
        quiz_repo: QuizScoreRepository,
    ):
        self.activity_repo = activity_repo
        self.risk_repo = risk_repo
        self.quiz_repo = quiz_repo

    def execute(self, enrollment_id: str):
        recency, streak = self.activity_repo.get_recency_and_streak(enrollment_id)
        quiz_avg = self.quiz_repo.get_average_quiz_score(enrollment_id)
        breakdown = compute_risk_breakdown(recency, streak, quiz_avg)
        # 축별 기여도·최대사유까지 저장 - 대시보드 목록의 '사유'와 상세의 '기여 요인' 막대가 이걸 읽는다.
        self.risk_repo.save_risk_score(
            enrollment_id,
            breakdown.score,
            breakdown.label,
            breakdown.contributions,
            breakdown.top_reason,
        )
        return breakdown


class NightlyReflowUseCase:
    """G정책 (a) 확정안: 매일 밤 누적 밀림량 판정 + 이번 주 남은 날짜만 재분배.
    Frozen Zone: 이 유스케이스는 '남은 날짜'만 건드리고 지나간 날짜/오늘 확정분은 손대지 않는다.
    수능 D-100 이내부터는 개인 슬립 여부와 무관하게 전반적으로 push_mode 강도를 상향한다."""

    def __init__(self, progress_repo: WeeklyProgressRepository, subscription_repo: SubscriptionRepository, clock=date.today):
        self.progress_repo = progress_repo
        self.subscription_repo = subscription_repo
        self.clock = clock

    def execute(self, enrollment_id: str):
        slip = self.progress_repo.get_cumulative_slip_minutes(enrollment_id)
        weekly_avg = self.progress_repo.get_weekly_average_minutes(enrollment_id)
        days_until_suneung = self._days_until_suneung(enrollment_id)
        status = compute_slip_status(slip, weekly_avg, days_until_suneung)

        remaining_lessons = self.progress_repo.get_remaining_lessons_this_week(enrollment_id)
        remaining_days = self.progress_repo.get_remaining_days_this_week(enrollment_id)
        daily_cap = self.progress_repo.get_daily_cap_minutes(enrollment_id)

        assignment = redistribute_remaining_week(
            remaining_lessons, remaining_days, status, daily_cap, days_until_suneung,
        )
        self.progress_repo.save_day_assignment(enrollment_id, assignment)
        return {"status": status, "assignment": assignment}

    def _days_until_suneung(self, enrollment_id: str) -> int | None:
        suneung_date = self.subscription_repo.get_suneung_date(enrollment_id)
        if suneung_date is None:
            return None
        return (suneung_date - self.clock()).days
