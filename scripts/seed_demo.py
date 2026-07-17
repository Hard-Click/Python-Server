"""발표 데모용 페르소나 시더 (hardclick_db 전용).

데모 DB에 베이스 픽스처 1코스 + 12페르소나를 심고, **검증된 순수 도메인 로직**(CP-SAT
스케줄러 / FSRS 복습 / 규칙기반 이탈위험)을 직접 호출해 산출물(weekly_schedule·
schedule_slot·review_card·review_log·dropout_risk)을 실제 스키마 테이블에 기록한다.
깨진 infrastructure/repositories.py(추정 스키마)는 거치지 않는다.

페르소나 구성:
  - 9201~9204 박모범/이눈치/최밀림/정위험 — 스케줄러·FSRS 서사용(앞 2인은 위험군 아님).
  - 9205~9212 위험군 8인 — 이탈관리 대시보드 목록용. 이게 없으면 목록이 2줄로 휑하다.
  → 목록(risk>=0.4) 노출 10명 = HIGH 5 / MEDIUM 5.

김첫날(콜드스타트)은 발표 당일 라이브 가입이라 시딩하지 않는다.

실행 (Python-Server 디렉토리에서):
  DB_HOST=127.0.0.1 DB_USER=Hard-Click DB_PASSWORD=Hard-Click DB_NAME=hardclick_db \
    python -m scripts.seed_demo

  # 발표 직전: 날짜 앵커를 발표일로 옮겨 재시딩(미지정 시 실행일 기준)
  SEED_TODAY=2026-07-27 DB_HOST=... python -m scripts.seed_demo
"""
import json
import os
import sys
from datetime import date, datetime, timedelta, timezone

import pymysql
from pymysql.cursors import DictCursor

from domain.scheduler import (
    compute_num_weeks, compute_efficiency_coefficient,
    generate_unified_weekly_schedule, compute_required_extension_weeks, SLIP_BUFFER_WEEKS,
)
from domain.review import review_lesson, quiz_score_to_grade
from domain.risk import compute_risk_breakdown

# 데모 기준일 — 활동이력·스케줄·복습 날짜가 전부 여기서 파생된다.
# 미지정 시 실행일. 시드가 과거에 박히면 "오늘 할 일/이번주" 화면이 비므로,
# 발표 직전 재시딩 때 SEED_TODAY=2026-07-27 처럼 앵커를 옮긴다.
_SEED_TODAY = os.environ.get("SEED_TODAY")
TODAY = date.fromisoformat(_SEED_TODAY) if _SEED_TODAY else date.today()
TODAY_DT = datetime(TODAY.year, TODAY.month, TODAY.day, 9, 0, tzinfo=timezone.utc)

# ID 대역 (데모 전용, 재실행 시 이 대역만 지움)
INSTRUCTOR_ID = 9200
# 9201~9204=서사용 4인(박모범/이눈치/최밀림/정위험), 9205~9212=이탈관리 목록 채우기용 위험군.
PERSONA_IDS = [9201, 9202, 9203, 9204, 9205, 9206, 9207, 9208, 9209, 9210, 9211, 9212]
LESSON_COUNT = 10
EXPECTED_MIN = 40                       # 강사 추정 강의시간(분)
EXPECTED_SEC = EXPECTED_MIN * 60

# 페르소나 공통 로그인 비번 = Flown2026!
# (이전 해시는 평문이 팀 내에 안 남아 있어 데모 때 아무도 로그인하지 못했다. 재시딩할 때마다
#  비번을 수동 UPDATE로 되맞추는 일이 없도록, 알려진 값의 해시를 시더가 직접 심는다.)
PW_HASH = "$2a$10$jyOD5ilYEfVfy2U91g5kUelb.eqh36wqv/yx928qAywPgZURHpzI."  # Flown2026!

# ── 페르소나 정의 ────────────────────────────────────────────────
# rest_days: 비트마스크(bit0=일 … bit6=토). completed: 완료 강의 수(나머지는 스케줄 대상).
# actual_sec: 완료 강의 1건당 실제 소요(초) → 효율계수. quiz: 완료/복습 퀴즈 점수 리스트(순환 사용).
# recency_days/miss_streak/last_gap: daily_achievement 백필로 만들 활동 신호.
PERSONAS = {
    9201: dict(name="박모범", username="p_model", enrolled_ago=60, target_weeks=12,
               daily_cap=120, rest_days=0b0000001, completed=6, actual_sec=2160,
               quiz=[95, 92, 98, 90, 93], grade=2,
               recency=1, miss_streak=0, dropout=False),
    9202: dict(name="이눈치", username="p_irregular", enrolled_ago=40, target_weeks=8,
               daily_cap=150, rest_days=0b0010101, completed=5, actual_sec=2400,
               quiz=[71, 68, 91, 73, 69], grade=4,
               recency=3, miss_streak=2, dropout=False),
    9203: dict(name="최밀림", username="p_behind", enrolled_ago=70, target_weeks=12,
               daily_cap=90, rest_days=0b0000001, completed=3, actual_sec=3600,
               quiz=[62, 58, 70, 65, 55], grade=5,
               recency=1, miss_streak=9, dropout=False),
    9204: dict(name="정위험", username="p_atrisk", enrolled_ago=50, target_weeks=6,
               daily_cap=60, rest_days=0b0000011, completed=3, actual_sec=4200,
               quiz=[48, 52, 45, 40, 55], grade=7,
               recency=22, miss_streak=20, dropout=True),

    # ── 이탈관리 대시보드 목록 채우기용 위험군 (9205~9212) ──
    # 위 4인은 스케줄러/FSRS 서사용이라 위험군이 2명뿐 → 관리자 화면이 2줄로 휑해진다.
    # recency/miss_streak/quiz는 domain.risk.compute_risk_breakdown 을 직접 돌려 등급을 맞춘 값
    # (계산 결과는 주석의 점수). recency가 12일을 넘으면 사실상 HIGH로 떨어진다.
    9205: dict(name="정하늘", username="p_risk1", enrolled_ago=60, target_weeks=12,
               daily_cap=90, rest_days=0b0000001, completed=2, actual_sec=4200,
               quiz=[30, 28, 32, 29, 31], grade=7,
               recency=21, miss_streak=21, dropout=True),      # ≈0.925 HIGH
    9206: dict(name="김민수", username="p_risk2", enrolled_ago=55, target_weeks=12,
               daily_cap=90, rest_days=0b0000001, completed=3, actual_sec=3900,
               quiz=[38, 35, 40, 36, 41], grade=6,
               recency=15, miss_streak=12, dropout=True),      # ≈0.905 HIGH
    9207: dict(name="강도윤", username="p_risk3", enrolled_ago=50, target_weeks=10,
               daily_cap=120, rest_days=0b0000001, completed=3, actual_sec=3600,
               quiz=[45, 43, 47, 44, 46], grade=6,
               recency=14, miss_streak=14, dropout=False),     # ≈0.887 HIGH
    9208: dict(name="이서연", username="p_risk4", enrolled_ago=45, target_weeks=10,
               daily_cap=120, rest_days=0b0000001, completed=4, actual_sec=3300,
               quiz=[55, 53, 57, 54, 56], grade=5,
               recency=18, miss_streak=10, dropout=False),     # ≈0.863 HIGH
    9209: dict(name="한예린", username="p_mid1", enrolled_ago=40, target_weeks=10,
               daily_cap=120, rest_days=0b0000001, completed=4, actual_sec=3000,
               quiz=[68, 66, 70, 67, 69], grade=4,
               recency=10, miss_streak=6, dropout=False),      # ≈0.659 MEDIUM
    9210: dict(name="배준호", username="p_mid2", enrolled_ago=38, target_weeks=10,
               daily_cap=120, rest_days=0b0000001, completed=4, actual_sec=3000,
               quiz=[60, 58, 62, 59, 61], grade=5,
               recency=5, miss_streak=7, dropout=False),       # ≈0.561 MEDIUM
    9211: dict(name="박지훈", username="p_mid3", enrolled_ago=35, target_weeks=10,
               daily_cap=120, rest_days=0b0000001, completed=5, actual_sec=2880,
               quiz=[41, 39, 43, 40, 42], grade=5,
               recency=2, miss_streak=7, dropout=False),       # ≈0.512 MEDIUM
    9212: dict(name="윤지아", username="p_mid4", enrolled_ago=35, target_weeks=10,
               daily_cap=120, rest_days=0b0000001, completed=5, actual_sec=2880,
               quiz=[66, 64, 68, 65, 67], grade=4,
               recency=2, miss_streak=8, dropout=False),       # ≈0.449 MEDIUM
}


def popcount(n):
    return bin(n).count("1")


def dt_str(d):
    """tz-aware/naive datetime → MySQL DATETIME 문자열."""
    if isinstance(d, datetime):
        return d.astimezone(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S") if d.tzinfo else d.strftime("%Y-%m-%d %H:%M:%S")
    return d.strftime("%Y-%m-%d %H:%M:%S")


def state_name(card):
    s = getattr(card, "state", None)
    nm = getattr(s, "name", str(s)).upper()
    return nm if nm in ("NEW", "LEARNING", "REVIEW", "RELEARNING") else "REVIEW"


class Seeder:
    def __init__(self, conn):
        self.conn = conn
        self.cur = conn.cursor()

    def x(self, sql, args=None):
        self.cur.execute(sql, args or ())
        return self.cur.lastrowid

    def q1(self, sql, args=None):
        self.cur.execute(sql, args or ())
        return self.cur.fetchone()

    # ── 재실행 대비 데모 데이터 삭제 ──
    def wipe(self):
        ids = ",".join(str(i) for i in PERSONA_IDS + [INSTRUCTOR_ID])
        self.cur.execute("SET FOREIGN_KEY_CHECKS=0")
        # 전역/알림: 데모 DB라 fsrs_params 전체 초기화, 페르소나 알림 정리
        self.cur.execute("DELETE FROM fsrs_params")
        self.cur.execute(f"DELETE FROM notification WHERE receiver_id IN ({ids})")
        # 코스: 강사가 만든 것만
        self.cur.execute("SELECT course_id FROM course WHERE author_id=%s", (INSTRUCTOR_ID,))
        course_ids = [r["course_id"] for r in self.cur.fetchall()]
        self.cur.execute("SELECT enrollment_id FROM enrollment WHERE member_id IN (%s)" % ids)
        enr_ids = [r["enrollment_id"] for r in self.cur.fetchall()]

        def del_in(table, col, vals):
            if vals:
                self.cur.execute(f"DELETE FROM {table} WHERE {col} IN ({','.join(['%s']*len(vals))})", vals)

        # enrollment 하위
        if enr_ids:
            self.cur.execute("SELECT id FROM weekly_schedule WHERE enrollment_id IN (%s)" % ",".join(['%s']*len(enr_ids)), enr_ids)
            ws_ids = [r["id"] for r in self.cur.fetchall()]
            del_in("schedule_slot", "weekly_schedule_id", ws_ids)
            del_in("weekly_schedule", "enrollment_id", enr_ids)
            self.cur.execute("SELECT id FROM review_card WHERE enrollment_id IN (%s)" % ",".join(['%s']*len(enr_ids)), enr_ids)
            rc_ids = [r["id"] for r in self.cur.fetchall()]
            del_in("review_log", "card_id", rc_ids)
            del_in("review_card", "enrollment_id", enr_ids)
            for t in ("daily_achievement", "dropout_risk", "dropout_event", "enrollment_onboarding", "student_availability"):
                del_in(t, "enrollment_id", enr_ids)
        # member 기준
        for t, col in (("enrollment", "member_id"), ("member_lesson_stat", "member_id"),
                       ("student_capacity", "student_id"), ("student_diagnostic_score", "member_id"),
                       ("quiz_submission", "member_id")):
            self.cur.execute(f"DELETE FROM {t} WHERE {col} IN ({ids})")
        # 코스 하위 (quiz/lesson/section)
        if course_ids:
            ph = ",".join(['%s']*len(course_ids))
            self.cur.execute(f"SELECT quiz_id FROM quiz WHERE course_id IN ({ph})", course_ids)
            quiz_ids = [r["quiz_id"] for r in self.cur.fetchall()]
            if quiz_ids:
                qph = ",".join(['%s']*len(quiz_ids))
                self.cur.execute(f"SELECT submission_id FROM quiz_submission WHERE quiz_id IN ({qph})", quiz_ids)
                sub_ids = [r["submission_id"] for r in self.cur.fetchall()]
                del_in("quiz_submission_answer", "submission_id", sub_ids)
                del_in("quiz_submission", "quiz_id", quiz_ids)
                del_in("lesson_quiz_map", "quiz_id", quiz_ids)
                self.cur.execute(f"SELECT question_id FROM quiz_question WHERE quiz_id IN ({qph})", quiz_ids)
                qq = [r["question_id"] for r in self.cur.fetchall()]
                del_in("quiz_option", "question_id", qq)
                del_in("quiz_question", "quiz_id", quiz_ids)
                del_in("quiz", "quiz_id", quiz_ids)
            self.cur.execute(f"SELECT id FROM course_section WHERE course_id IN ({ph})", course_ids)
            sec_ids = [r["id"] for r in self.cur.fetchall()]
            if sec_ids:
                sph = ",".join(['%s']*len(sec_ids))
                self.cur.execute(f"SELECT id FROM lesson WHERE section_id IN ({sph})", sec_ids)
                les = [r["id"] for r in self.cur.fetchall()]
                del_in("lesson_prerequisite", "lesson_id", les)
                del_in("member_lesson_stat", "lesson_id", les)
                del_in("lesson", "section_id", sec_ids)
                del_in("course_section", "course_id", course_ids)
            del_in("course_learning_policy", "course_id", course_ids)
            del_in("course", "course_id", course_ids)
        self.cur.execute(f"DELETE FROM members WHERE member_id IN ({ids})")
        self.cur.execute("SET FOREIGN_KEY_CHECKS=1")

    def member(self, mid, name, username, role):
        self.x("""INSERT INTO members
            (member_id, name, email, username, password, role, status,
             is_locked, is_password_change_required, login_fail_count, optional_terms_agreed, created_at)
            VALUES (%s,%s,%s,%s,%s,%s,'ACTIVE', 0,0,0,1,%s)""",
               (mid, name, f"{username}@flown.demo", username, PW_HASH, role, dt_str(TODAY_DT)))

    def fixtures(self):
        """강사 + 코스1 + 섹션1 + lesson N + 선수관계 + 정책 + 퀴즈(강의당 1) + fsrs global."""
        self.member(INSTRUCTOR_ID, "김강사데모", "demo_inst", "INSTRUCTOR")
        course_id = self.x("""INSERT INTO course
            (author_id, price, title, created_at, description, price_type, status, subject)
            VALUES (%s, 0, %s, %s, %s, 'FREE', 'PUBLISHED', %s)""",
            (INSTRUCTOR_ID, "수능 국어 완성 (데모)", dt_str(TODAY_DT), "데모용 코스", "국어"))
        section_id = self.x("INSERT INTO course_section (order_index, title, course_id) VALUES (1,%s,%s)",
                            ("전체", course_id))
        lessons = []
        for i in range(1, LESSON_COUNT + 1):
            lid = self.x("""INSERT INTO lesson (created_at, order_index, title, section_id, duration_seconds)
                VALUES (%s,%s,%s,%s,%s)""",
                (dt_str(TODAY_DT), i, f"{i}강", section_id, EXPECTED_SEC))
            lessons.append(lid)
        # 선수관계: 순차(각 강의는 직전 강의 선수) - CP-SAT 순서 제약 시연
        for a, b in zip(lessons, lessons[1:]):
            self.x("INSERT INTO lesson_prerequisite (lesson_id, prerequisite_lesson_id) VALUES (%s,%s)", (b, a))
        # 코스 정책 (권장 완강 12주, 하루권장 120)
        self.x("""INSERT INTO course_learning_policy
            (course_id, recommended_duration_weeks, daily_recommended_minutes, difficulty, weekly_max_load_min)
            VALUES (%s, 12, 120, 'MEDIUM', 840)""", (course_id,))
        # 강의당 퀴즈 1개(문항 3, 보기 4) + lesson_quiz_map
        quiz_by_lesson = {}
        for idx, lid in enumerate(lessons, 1):
            qid = self.x("INSERT INTO quiz (course_id, section_id, instructor_id, title) VALUES (%s,%s,%s,%s)",
                        (course_id, section_id, INSTRUCTOR_ID, f"{idx}강 퀴즈"))
            self.x("INSERT INTO lesson_quiz_map (lesson_id, quiz_id) VALUES (%s,%s)", (lid, qid))
            qmeta = []
            for qn in range(1, 4):
                qqid = self.x("INSERT INTO quiz_question (quiz_id, question_number, question_text) VALUES (%s,%s,%s)",
                             (qid, qn, f"{idx}강 {qn}번 문제"))
                opts = []
                for on in range(1, 5):
                    oid = self.x("INSERT INTO quiz_option (question_id, option_number, option_text, is_correct) VALUES (%s,%s,%s,%s)",
                                (qqid, on, f"보기{on}", 1 if on == 1 else 0))
                    opts.append(oid)
                qmeta.append((qqid, opts))
            quiz_by_lesson[lid] = (qid, qmeta)
        # FSRS global params
        try:
            from fsrs import Scheduler
            weights = list(getattr(Scheduler(), "parameters", []) or [])
        except Exception:
            weights = []
        self.x("""INSERT INTO fsrs_params (scope, student_id, weights, retention_target)
            VALUES ('GLOBAL', NULL, %s, 0.9)""", (json.dumps(weights),))
        return course_id, section_id, lessons, quiz_by_lesson

    def persona(self, mid, cfg, course_id, section_id, lessons, quiz_by_lesson):
        self.member(mid, cfg["name"], cfg["username"], "STUDENT")
        enrolled_at = TODAY - timedelta(days=cfg["enrolled_ago"])
        enrollment_id = self.x("""INSERT INTO enrollment
            (course_id, enrolled_at, member_id, status, target_weeks, target_weeks_original)
            VALUES (%s,%s,%s,'IN_PROGRESS',%s,%s)""",
            (course_id, dt_str(datetime.combine(enrolled_at, datetime.min.time())),
             mid, cfg["target_weeks"], cfg["target_weeks"]))
        self.x("INSERT INTO enrollment_onboarding (enrollment_id, rest_days, onboarded_at) VALUES (%s,%s,%s)",
               (enrollment_id, cfg["rest_days"], dt_str(datetime.combine(enrolled_at, datetime.min.time()))))
        # V3.2.4: rest_days·onboarded_at는 student_capacity(학생 단위)로 이동됨. onboarded_at 채워 온보딩 완료로 인식시킴.
        self.x("INSERT INTO student_capacity (student_id, daily_cap_min, rest_days, onboarded_at) VALUES (%s,%s,%s,%s)",
               (mid, cfg["daily_cap"], cfg["rest_days"], dt_str(datetime.combine(enrolled_at, datetime.min.time()))))
        # 가용시간: 학습일마다 저녁 19-22시. V3.2.3: student_availability는 enrollment_id → member_id(학생 단위).
        for dow in range(7):
            if not (cfg["rest_days"] >> dow) & 1:
                self.x("INSERT INTO student_availability (member_id, day_of_week, start_time, end_time) VALUES (%s,%s,'19:00:00','22:00:00')",
                       (mid, dow))
        self.x("INSERT INTO student_diagnostic_score (member_id, course_id, grade, exam_date) VALUES (%s,%s,%s,%s)",
               (mid, course_id, cfg["grade"], dt_str(TODAY - timedelta(days=30))[:10]))

        completed = lessons[:cfg["completed"]]
        remaining = lessons[cfg["completed"]:]

        # ── member_lesson_stat (완료 강의 실측) ──
        for i, lid in enumerate(completed):
            self.x("""INSERT INTO member_lesson_stat
                (member_id, lesson_id, actual_completion_sec, rewatch_count, last_studied_at)
                VALUES (%s,%s,%s,%s,%s)""",
                (mid, lid, cfg["actual_sec"], 2 if cfg["actual_sec"] > EXPECTED_SEC else 0,
                 dt_str(datetime.combine(TODAY - timedelta(days=cfg["recency"]), datetime.min.time()))))

        # ── 효율계수 ──
        completed_for_eff = [{"expected_duration_min": EXPECTED_MIN,
                              "actual_duration_min": cfg["actual_sec"] // 60} for _ in completed]
        coeff = compute_efficiency_coefficient(completed_for_eff)

        # ── 퀴즈 제출 + 오답(유사문제 AI 입력) + FSRS 복습 ──
        quiz_scores_used = []
        for i, lid in enumerate(completed):
            score = cfg["quiz"][i % len(cfg["quiz"])]
            quiz_scores_used.append(score)
            qid, qmeta = quiz_by_lesson[lid]
            total_q = len(qmeta)
            correct = round(total_q * score / 100)
            submitted = datetime.combine(TODAY - timedelta(days=cfg["recency"] + (len(completed) - i)), datetime.min.time())
            sub_id = self.x("""INSERT INTO quiz_submission
                (quiz_id, member_id, score, total_question_count, correct_count, submitted_at)
                VALUES (%s,%s,%s,%s,%s,%s)""",
                (qid, mid, score, total_q, correct, dt_str(submitted)))
            for qn, (qqid, opts) in enumerate(qmeta):
                is_corr = 1 if qn < correct else 0
                self.x("""INSERT INTO quiz_submission_answer
                    (submission_id, question_id, selected_option_id, is_correct)
                    VALUES (%s,%s,%s,%s)""",
                    (sub_id, qqid, opts[0] if is_corr else opts[1], is_corr))

            # FSRS: 이 카드에 대해 여러 번 복습 (aggregate 16+ 로그 목표).
            # 카드가 있어야 review_log.card_id를 채우므로 먼저 review_card(placeholder)를 만들고
            # 리뷰를 진행하며 로그를 붙인 뒤, 최종 카드 상태로 review_card를 UPDATE한다.
            card_id = self.x("""INSERT INTO review_card
                (enrollment_id, lesson_id, state, reps, lapses, scheduled_days)
                VALUES (%s,%s,'NEW',0,0,0)""", (enrollment_id, lid))
            card = None
            n_rev = 4
            prev_rev_dt = None
            for r in range(n_rev):
                rscore = cfg["quiz"][(i + r) % len(cfg["quiz"])]
                days_ago = max(cfg["recency"], (cfg["enrolled_ago"] - 5) - r * (cfg["enrolled_ago"] // (n_rev + 1)))
                rev_dt = TODAY_DT - timedelta(days=days_ago)
                card, due = review_lesson(card, rscore, review_datetime=rev_dt, max_interval_days=180)
                rating = int(quiz_score_to_grade(rscore))
                elapsed = 0 if prev_rev_dt is None else max(0, (rev_dt - prev_rev_dt).days)
                self.x("""INSERT INTO review_log (card_id, rating, quiz_score, reviewed_at, elapsed_days, scheduled_days)
                    VALUES (%s,%s,%s,%s,%s,%s)""",
                    (card_id, rating, rscore, dt_str(rev_dt), elapsed, max(0, (due - rev_dt).days)))
                prev_rev_dt = rev_dt
            self.cur.execute("""UPDATE review_card SET stability=%s, difficulty=%s, due=%s,
                last_review=%s, state=%s, reps=%s, scheduled_days=%s WHERE id=%s""",
                (round(float(card.stability), 4), round(float(card.difficulty), 4),
                 dt_str(card.due), dt_str(getattr(card, "last_review", None) or rev_dt),
                 state_name(card), n_rev, max(0, (card.due - rev_dt).days), card_id))

        avg_quiz = sum(quiz_scores_used) / len(quiz_scores_used) if quiz_scores_used else None

        # ── daily_achievement 백필 (recency/streak/slip 신호) ──
        study_days = max(1, 7 - popcount(cfg["rest_days"]))
        daily_planned = cfg["daily_cap"]
        for d in range(cfg["enrolled_ago"], 0, -1):
            adate = TODAY - timedelta(days=d)
            # 마지막 recency일 동안은 미달(정위험/최밀림 streak 재현)
            in_streak = d <= cfg["miss_streak"]
            achieved = 0 if in_streak else (0 if (d % 7 == 0) else 1)  # 쉬는날 근사 미달
            if cfg["dropout"] and d <= cfg["recency"]:
                achieved = 0
            actual = 0 if achieved == 0 else daily_planned
            self.x("""INSERT INTO daily_achievement
                (enrollment_id, achieved_date, planned_min, actual_min, achieved)
                VALUES (%s,%s,%s,%s,%s)""",
                (enrollment_id, dt_str(adate)[:10], daily_planned, actual, achieved))

        # ── 이탈위험 (규칙기반) ──
        breakdown = compute_risk_breakdown(cfg["recency"], cfg["miss_streak"], avg_quiz)
        self.x("""INSERT INTO dropout_risk
            (enrollment_id, computed_at, risk_score, method, recency_days, miss_streak, features)
            VALUES (%s,%s,%s,'RULE',%s,%s,%s)""",
            (enrollment_id, dt_str(TODAY_DT), breakdown.score, cfg["recency"], cfg["miss_streak"],
             json.dumps({"label": breakdown.label, "top_reason": breakdown.top_reason,
                         "contributions": breakdown.contributions}, ensure_ascii=False)))
        # Cox PH 라벨
        if cfg["dropout"]:
            self.x("""INSERT INTO dropout_event (enrollment_id, event_occurred, event_date, censored, observed_days)
                VALUES (%s,1,%s,0,%s)""",
                (enrollment_id, dt_str(TODAY - timedelta(days=cfg["recency"]))[:10], cfg["enrolled_ago"]))
        else:
            self.x("""INSERT INTO dropout_event (enrollment_id, event_occurred, event_date, censored, observed_days)
                VALUES (%s,0,NULL,1,%s)""", (enrollment_id, cfg["enrolled_ago"]))

        # ── 스케줄 (CP-SAT) ──
        num_weeks = compute_num_weeks(TODAY, enrolled_at, cfg["target_weeks"], None)
        adjusted = max(1, round(EXPECTED_MIN * coeff))
        monday = TODAY - timedelta(days=TODAY.weekday())

        # ── 완료분을 과거 DONE 슬롯으로 백필 ──
        # forward 스케줄에는 remaining(미완료)만 들어가므로, 이것만 심으면 BE의
        # progressRate(= DONE 슬롯 / 전체 슬롯)가 모든 학생에게 0으로 나온다.
        # 완료 강의를 지난 주차 슬롯(DONE·locked)으로 남겨 진도율이 페르소나별로 갈리게 한다.
        if completed:
            weeks_back = max(1, -(-len(completed) // study_days))  # ceil division
            past_ws_id = self.x("""INSERT INTO weekly_schedule
                (enrollment_id, week_no, generated_at, reflow_reason, locked, effective_from)
                VALUES (%s,%s,%s,%s,1,%s)""",
                (enrollment_id, -1, dt_str(TODAY_DT), "완료분 기록(데모 시드)",
                 dt_str(monday - timedelta(weeks=weeks_back))[:10]))
            for i, lid in enumerate(completed):
                done_date = monday - timedelta(days=len(completed) - i)
                self.x("""INSERT INTO schedule_slot
                    (weekly_schedule_id, lesson_id, plan_date, start_time, planned_min, status)
                    VALUES (%s,%s,%s,'19:00:00',%s,'DONE')""",
                    (past_ws_id, lid, dt_str(done_date)[:10], adjusted))
        cpsat_lessons = [{"id": lid, "course_id": course_id, "duration_min": adjusted,
                          "deadline_week": num_weeks - 1} for lid in remaining]
        weekly_avail = daily_planned * study_days
        prereqs = [(a, b) for a, b in zip(lessons, lessons[1:]) if a in remaining and b in remaining]
        caps = [weekly_avail] * num_weeks
        assignment = generate_unified_weekly_schedule(cpsat_lessons, caps, prereqs, {course_id: cfg["grade"]})
        ext = 0
        if assignment is None and remaining:
            totals = [{"total_duration_min": adjusted * len(remaining), "deadline_week": num_weeks - 1}]
            ext = compute_required_extension_weeks(totals, weekly_avail, SLIP_BUFFER_WEEKS)
            if ext > 0:
                caps = [weekly_avail] * (num_weeks + ext)
                for l in cpsat_lessons:
                    l["deadline_week"] = num_weeks + ext - 1
                assignment = generate_unified_weekly_schedule(cpsat_lessons, caps, prereqs, {course_id: cfg["grade"]})

        status = "OK"
        if assignment is None:
            status = "INFEASIBLE"
            self.x("""INSERT INTO notification (created_at, is_read, message, receiver_id, type)
                VALUES (%s,0,%s,%s,'NOTICE')""",
                (dt_str(TODAY_DT), "지금 설정으로는 목표기간 내 완주가 어려워요. 목표기간이나 학습량을 조정해주세요.", mid))
        else:
            total_weeks = num_weeks + ext
            reason = f"초기 생성 (효율계수 {coeff:.2f}, {total_weeks}주 배정" + (f", +{ext}주 연장)" if ext else ")")
            # 주차별 weekly_schedule + schedule_slot
            by_week = {}
            for lid, wk in assignment.items():
                by_week.setdefault(wk, []).append(lid)
            for wk, lids in sorted(by_week.items()):
                ws_id = self.x("""INSERT INTO weekly_schedule
                    (enrollment_id, week_no, generated_at, reflow_reason, locked, effective_from)
                    VALUES (%s,%s,%s,%s,0,%s)""",
                    (enrollment_id, wk, dt_str(TODAY_DT), reason,
                     dt_str(monday + timedelta(weeks=wk))[:10]))
                for j, lid in enumerate(lids):
                    plan_date = monday + timedelta(weeks=wk, days=min(j, 6))
                    self.x("""INSERT INTO schedule_slot
                        (weekly_schedule_id, lesson_id, plan_date, start_time, planned_min, status)
                        VALUES (%s,%s,%s,'19:00:00',%s,'PLANNED')""",
                        (ws_id, lid, dt_str(plan_date)[:10], adjusted))

        return dict(name=cfg["name"], enrollment_id=enrollment_id, coeff=round(coeff, 2),
                    num_weeks=num_weeks, ext=ext, weekly_avail=weekly_avail, remaining=len(remaining),
                    avg_quiz=round(avg_quiz, 1) if avg_quiz else None,
                    risk=breakdown.score, risk_label=breakdown.label, top_reason=breakdown.top_reason,
                    status=status)


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    conn = pymysql.connect(
        host=os.environ.get("DB_HOST", "127.0.0.1"),
        port=int(os.environ.get("DB_PORT", 3306)),
        user=os.environ["DB_USER"], password=os.environ["DB_PASSWORD"],
        database=os.environ.get("DB_NAME", "hardclick_db"),
        cursorclass=DictCursor, autocommit=False,
    )
    s = Seeder(conn)
    print("wiping demo data…")
    s.wipe()
    print("seeding fixtures…")
    course_id, section_id, lessons, quiz_by_lesson = s.fixtures()
    results = []
    for mid, cfg in PERSONAS.items():
        print(f"seeding {cfg['name']} ({mid})…")
        results.append(s.persona(mid, cfg, course_id, section_id, lessons, quiz_by_lesson))
    conn.commit()

    print("\n=== 페르소나 요약 ===")
    hdr = f"{'페르소나':<8}{'효율':>6}{'주수':>5}{'연장':>5}{'주간가용':>8}{'남은강의':>8}{'평균퀴즈':>8}{'위험':>7}{'라벨':>8}{'사유':>10}{'스케줄':>10}"
    print(hdr)
    for r in results:
        print(f"{r['name']:<8}{r['coeff']:>6}{r['num_weeks']:>5}{r['ext']:>5}{r['weekly_avail']:>8}"
              f"{r['remaining']:>8}{str(r['avg_quiz']):>8}{r['risk']:>7}{r['risk_label']:>8}{r['top_reason']:>10}{r['status']:>10}")
    conn.close()


if __name__ == "__main__":
    main()
