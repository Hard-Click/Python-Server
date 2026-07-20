"""발표 데모용 페르소나 시더 (hardclick_db 전용).

데모 DB에 베이스 픽스처 1코스 + 12페르소나를 심고, **검증된 순수 도메인 로직**(CP-SAT
스케줄러 / FSRS 복습 / 규칙기반 이탈위험)을 직접 호출해 산출물(weekly_schedule·
schedule_slot·review_card·review_log·dropout_risk)을 실제 스키마 테이블에 기록한다.
깨진 infrastructure/repositories.py(추정 스키마)는 거치지 않는다.

페르소나 구성:
  - 9201~9204 박모범/이눈치/최밀림/정위험 — 스케줄러·FSRS 서사용(앞 2인은 위험군 아님).
  - 9205~9212 위험군 8인 — 이탈관리 대시보드 목록용. 이게 없으면 목록이 2줄로 휑하다.
  - 9213~9214 qa_full/qa_edge — FE UI 확인 전용(서사 무관·churn 목록 비노출). 페르소나
    계정을 FE가 만지면 최근접속 등이 오염되므로 QA는 반드시 이 둘로.
  → 목록(risk>=0.4) 노출 10명 = HIGH 5 / MEDIUM 5 (QA 2계정은 LOW라 제외).

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

try:
    from scripts.math_quiz_bank import MATH_QUIZ_BANK   # 윤종호 수학 100문제 (course 17·21)
except ImportError:
    from math_quiz_bank import MATH_QUIZ_BANK

# 메가스터디 썸네일 → S3 업로드본 URL. next/image 는 data URI 불가·public S3 URL만 허용.
# build_demo_thumbnails.py 가 scripts/demo_thumbs/*.jpg + 키 매핑 생성. S3_BUCKET env 없으면 Unsplash 폴백.
try:
    from scripts.demo_thumbnails import S3_PREFIX, DEMO_THUMB_KEYS, CATALOG_THUMB_KEYS
except Exception:
    try:
        from demo_thumbnails import S3_PREFIX, DEMO_THUMB_KEYS, CATALOG_THUMB_KEYS
    except Exception:
        S3_PREFIX, DEMO_THUMB_KEYS, CATALOG_THUMB_KEYS = "thumbnails/demo", {}, []
_S3_BUCKET = os.environ.get("S3_BUCKET")
S3_PREFIX = os.environ.get("S3_THUMB_PREFIX") or S3_PREFIX   # 업로드 위치 다르면 런타임 override


def _s3_thumb(key):
    if _S3_BUCKET and key:
        return f"https://{_S3_BUCKET}.s3.ap-northeast-2.amazonaws.com/{S3_PREFIX}/{key}"
    return None

# 데모 기준일 — 활동이력·스케줄·복습 날짜가 전부 여기서 파생된다.
# 미지정 시 실행일. 시드가 과거에 박히면 "오늘 할 일/이번주" 화면이 비므로,
# 발표 직전 재시딩 때 SEED_TODAY=2026-07-27 처럼 앵커를 옮긴다.
_SEED_TODAY = os.environ.get("SEED_TODAY")
TODAY = date.fromisoformat(_SEED_TODAY) if _SEED_TODAY else date.today()
TODAY_DT = datetime(TODAY.year, TODAY.month, TODAY.day, 9, 0, tzinfo=timezone.utc)

# ID 대역 (데모 전용, 재실행 시 이 대역만 지움)
INSTRUCTOR_ID = 9200
# 9201~9204=서사용 4인(박모범/이눈치/최밀림/정위험), 9205~9212=이탈관리 목록 채우기용 위험군.
# 9213~9214=FE QA 전용(페르소나 아님) — FE가 페르소나 계정에 로그인하면 최근접속·이력이
# 오염되므로 UI 확인은 이 둘로만 한다. 위험도 LOW라 관리자 churn 목록(>=0.4)에 안 뜬다.
PERSONA_IDS = [9201, 9202, 9203, 9204, 9205, 9206, 9207, 9208, 9209, 9210, 9211, 9212,
               9213, 9214]
# 카탈로그(강의 둘러보기) 채우기용 강사 10명 — 각자 코스 1개 + 재생되는 영상 강의 4개.
CATALOG_INSTRUCTOR_IDS = list(range(9220, 9230))

# 실제로 재생되는 공개 영상(HTTPS·archive.org 공개영화, 임베드 가능). (url, 실제 길이 초).
# 재생 어댑터가 s3_key 없으면 video_url을 그대로 스트리밍하므로, S3 업로드 없이 이 URL만으로 재생된다.
DEMO_VIDEOS = [
    ("https://archive.org/download/BigBuckBunny_124/Content/big_buck_bunny_720p_surround.mp4", 596),
    ("https://archive.org/download/Sintel/sintel-2048-surround_512kb.mp4", 888),
    ("https://archive.org/download/ElephantsDream/ed_1024_512kb.mp4", 654),
]

# 코스 썸네일(공부/과목 테마, Unsplash HTTPS·핫링크 허용). 200 확인된 사진만 사용.
def _thumb(pid):
    return f"https://images.unsplash.com/photo-{pid}?w=800&h=450&fit=crop"

DEMO_COURSE_THUMB = _thumb("1456513080510-7bf3a84b82f8")   # 노트북+공부 (데모 코스)
# 카탈로그 강사 순서와 1:1 (국어/영어/한국사/국어/영어/사회/과학/국어)
# 수학 2코스(수Ⅰ·Ⅱ·선택과목)는 DEMO_COURSES 로 승격돼 여기서 제외(중복 방지).
CATALOG_THUMBS = [
    _thumb("1481627834876-b7833e8f5570"),   # 도서관 책 (국어 비문학)
    _thumb("1513258496099-48168024aec0"),   # 영어 글자 (어휘)
    _thumb("1596495578065-6e0763fa1178"),   # 지구본 (한국사)
    _thumb("1497633762265-9d179a990aa6"),   # 책 더미 (국어 문학)
    _thumb("1503676260728-1c00da094a0b"),   # 책상 공부 (영어 구문)
    _thumb("1524995997946-a1c2e315a42f"),   # 온라인 학습 (사탐)
    _thumb("1594322436404-5a0526db4d13"),   # 과학 실험 (과탐)
    _thumb("1546410531-bb4caa6b424d"),      # 시험지 (화작)
]
LESSON_COUNT = 10
EXPECTED_MIN = 40                       # 강사 추정 강의시간(분)
EXPECTED_SEC = EXPECTED_MIN * 60

# ── 데모 수강 코스 3개 (전 페르소나 공통 수강) ────────────────────────
# grade = 과목별 진단 등급(전 페르소나 동일, 낮을수록 잘함). 수학은 취약과목(원점수 55 ≈ 5등급)
#   → CP-SAT 가중치·정책·가용시간 배분으로 학습시간을 더 준다.
# avail_frac = 학생 주간 가용시간을 코스별로 나누는 비율(합 1.0). 취약 수학에 더 배분.
# start_time = 슬롯 표시 시각(코스별로 어긋나게 해 캘린더에서 안 겹치게).
#   bank!=None 이면 MATH_QUIZ_BANK[bank]의 섹션(=강의)마다 실문제 10개 퀴즈. lessons=강의 수.
#   수학은 취약(5등급) + avail_frac 합 0.44 로 학습시간 더 배분. avail_frac 4코스 합=1.0.
DEMO_COURSES = [
    dict(key="kor",   title="수능 국어 완성 (데모)",          subject="국어",     grade=3,
         lessons=10, bank=None,    weeks=12, daily=100, difficulty="MEDIUM", weekly_max=700,
         avail_frac=0.28, start_time="19:00:00", thumb=DEMO_COURSE_THUMB),
    dict(key="math1", title="수학Ⅰ·Ⅱ 개념 완성",             subject="수학",     grade=5,
         lessons=5,  bank="math1", weeks=12, daily=140, difficulty="HARD",   weekly_max=980,
         avail_frac=0.24, start_time="20:00:00", thumb=_thumb("1509228468518-180dd4864904")),
    dict(key="math2", title="수학 선택과목(확통·미적) 특강",  subject="수학",     grade=5,
         lessons=5,  bank="math2", weeks=12, daily=120, difficulty="HARD",   weekly_max=840,
         avail_frac=0.20, start_time="21:00:00", thumb=_thumb("1434030216411-0b793f4b4173")),
    dict(key="soc",   title="사회탐구 개념 완성 (데모)",      subject="사회탐구", grade=4,
         lessons=10, bank=None,    weeks=10, daily=90,  difficulty="MEDIUM", weekly_max=630,
         avail_frac=0.28, start_time="19:30:00", thumb=_thumb("1524995997946-a1c2e315a42f")),
]
DEMO_COURSE_BY_KEY = {c["key"]: c for c in DEMO_COURSES}
PRIMARY_COURSE_KEY = "kor"   # 이탈위험·활동이력은 이 enrollment 하나에만 심음(학생당 1줄)

# 페르소나 공통 로그인 비번 = Flown2026!
# (이전 해시는 평문이 팀 내에 안 남아 있어 데모 때 아무도 로그인하지 못했다. 재시딩할 때마다
#  비번을 수동 UPDATE로 되맞추는 일이 없도록, 알려진 값의 해시를 시더가 직접 심는다.)
PW_HASH = "$2a$10$jyOD5ilYEfVfy2U91g5kUelb.eqh36wqv/yx928qAywPgZURHpzI."  # Flown2026!

# ── 페르소나 정의 ────────────────────────────────────────────────
# rest_days: 비트마스크(bit0=일 … bit6=토). completed: {코스키: 완료 강의 수}(나머지는 스케줄 대상).
#   → completed 의 키가 그 페르소나가 수강하는 코스다. 서사 4인=국/수/탐 3코스, 배경 위험군=국어만.
# 진단 등급(grade)은 코스별(DEMO_COURSES.grade, 전 페르소나 동일) — 여기선 안 둔다(습관만 차이).
# actual_sec: 완료 강의 1건당 실제 소요(초) → 효율계수. quiz: 완료/복습 퀴즈 점수 리스트(순환 사용).
# recency_days/miss_streak/last_gap: daily_achievement 백필로 만들 활동 신호(학생 단위).
PERSONAS = {
    # 수학 완료수(matrix)는 math1(수Ⅰ·Ⅱ)+math2(선택과목) 합: 박6=4+2, 이3=2+1, 최2=1+1, 정1=1+0.
    9201: dict(name="박모범", username="p_model", enrolled_ago=60, target_weeks=12,
               daily_cap=120, rest_days=0b0000001,
               completed={"kor": 6, "math1": 4, "math2": 2, "soc": 5},
               actual_sec=2160, quiz=[95, 92, 98, 90, 93],
               recency=1, miss_streak=0, dropout=False),
    9202: dict(name="이눈치", username="p_irregular", enrolled_ago=40, target_weeks=8,
               daily_cap=150, rest_days=0b0010101,
               completed={"kor": 5, "math1": 2, "math2": 1, "soc": 4},
               actual_sec=2400, quiz=[71, 68, 91, 73, 69],
               recency=3, miss_streak=2, dropout=False),
    9203: dict(name="최밀림", username="p_behind", enrolled_ago=70, target_weeks=12,
               daily_cap=90, rest_days=0b0000001,
               completed={"kor": 3, "math1": 1, "math2": 1, "soc": 2},
               actual_sec=3600, quiz=[62, 58, 70, 65, 55],
               recency=1, miss_streak=9, dropout=False),
    9204: dict(name="정위험", username="p_atrisk", enrolled_ago=50, target_weeks=6,
               daily_cap=60, rest_days=0b0000011,
               completed={"kor": 2, "math1": 1, "math2": 0, "soc": 1},
               actual_sec=4200, quiz=[48, 52, 45, 40, 55],
               recency=22, miss_streak=20, dropout=True),

    # ── 이탈관리 대시보드 목록 채우기용 위험군 (9205~9212) — 국어 1코스만 ──
    # 위 4인은 스케줄러/FSRS 서사용이라 위험군이 2명뿐 → 관리자 화면이 2줄로 휑해진다.
    # recency/miss_streak/quiz는 domain.risk.compute_risk_breakdown 을 직접 돌려 등급을 맞춘 값
    # (계산 결과는 주석의 점수). recency가 12일을 넘으면 사실상 HIGH로 떨어진다.
    9205: dict(name="정하늘", username="p_risk1", enrolled_ago=60, target_weeks=12,
               daily_cap=90, rest_days=0b0000001, completed={"kor": 2}, actual_sec=4200,
               quiz=[30, 28, 32, 29, 31],
               recency=21, miss_streak=21, dropout=True),      # ≈0.925 HIGH
    9206: dict(name="김민수", username="p_risk2", enrolled_ago=55, target_weeks=12,
               daily_cap=90, rest_days=0b0000001, completed={"kor": 3}, actual_sec=3900,
               quiz=[38, 35, 40, 36, 41],
               recency=15, miss_streak=12, dropout=True),      # ≈0.905 HIGH
    9207: dict(name="강도윤", username="p_risk3", enrolled_ago=50, target_weeks=10,
               daily_cap=120, rest_days=0b0000001, completed={"kor": 3}, actual_sec=3600,
               quiz=[45, 43, 47, 44, 46],
               recency=14, miss_streak=14, dropout=False),     # ≈0.887 HIGH
    9208: dict(name="이서연", username="p_risk4", enrolled_ago=45, target_weeks=10,
               daily_cap=120, rest_days=0b0000001, completed={"kor": 4}, actual_sec=3300,
               quiz=[55, 53, 57, 54, 56],
               recency=18, miss_streak=10, dropout=False),     # ≈0.863 HIGH
    9209: dict(name="한예린", username="p_mid1", enrolled_ago=40, target_weeks=10,
               daily_cap=120, rest_days=0b0000001, completed={"kor": 4}, actual_sec=3000,
               quiz=[68, 66, 70, 67, 69],
               recency=10, miss_streak=6, dropout=False),      # ≈0.659 MEDIUM
    9210: dict(name="배준호", username="p_mid2", enrolled_ago=38, target_weeks=10,
               daily_cap=120, rest_days=0b0000001, completed={"kor": 4}, actual_sec=3000,
               quiz=[60, 58, 62, 59, 61],
               recency=5, miss_streak=7, dropout=False),       # ≈0.561 MEDIUM
    9211: dict(name="박지훈", username="p_mid3", enrolled_ago=35, target_weeks=10,
               daily_cap=120, rest_days=0b0000001, completed={"kor": 5}, actual_sec=2880,
               quiz=[41, 39, 43, 40, 42],
               recency=2, miss_streak=7, dropout=False),       # ≈0.512 MEDIUM
    9212: dict(name="윤지아", username="p_mid4", enrolled_ago=35, target_weeks=10,
               daily_cap=120, rest_days=0b0000001, completed={"kor": 5}, actual_sec=2880,
               quiz=[66, 64, 68, 65, 67],
               recency=2, miss_streak=8, dropout=False),       # ≈0.449 MEDIUM

    # ── FE QA 전용 (9213~9214) — 페르소나 서사와 완전 분리, 마음껏 로그인/클릭해도 됨 ──
    # qa_full: 박모범과 같은 "꽉 찬" 데이터(4코스·완료 다수·스케줄·복습카드) — 일반 학생 UI 확인용.
    # qa_edge: 정위험과 같은 capacity/target(스케줄 INFEASIBLE + 알림 발생)이지만 활동신호는
    #   정상(recency 1·streak 0 → risk≈0.16 LOW)이라 churn 목록엔 안 뜸 — 빈 상태·경고 UI 확인용.
    9213: dict(name="큐에이풀", username="qa_full", enrolled_ago=60, target_weeks=12,
               daily_cap=120, rest_days=0b0000001,
               completed={"kor": 6, "math1": 4, "math2": 2, "soc": 5},
               actual_sec=2160, quiz=[95, 92, 98, 90, 93],
               recency=1, miss_streak=0, dropout=False),       # ≈0.048 LOW (목록 비노출)
    9214: dict(name="큐에이엣지", username="qa_edge", enrolled_ago=50, target_weeks=6,
               daily_cap=60, rest_days=0b0000011,
               completed={"kor": 2, "math1": 1, "math2": 0, "soc": 1},
               actual_sec=4200, quiz=[48, 52, 45, 40, 55],
               recency=1, miss_streak=0, dropout=False),       # ≈0.16 LOW (목록 비노출)
}

# ── 카탈로그 강사/코스 (둘러보기 화면 채우기용, 학생 미수강) ───────────────
# member_id는 9220부터. 코스당 재생되는 영상 강의 4개. 페르소나 서사와 무관.
CATALOG_INSTRUCTORS = [
    dict(name="한지문", subject="국어", one_line="비문학 독해 12년, 지문이 눈에 들어오게",
         course="수능 국어 비문학 독해 전략",
         career="전) 대형학원 국어 대표강사 · 수능 비문학 교재 3종 집필",
         intro="지문을 '읽는 법'부터 다시 잡습니다. 감이 아니라 근거로 답을 고르게."),
    dict(name="김보카", subject="영어", one_line="하루 30단어, 수능 어휘 정복",
         course="수능 영어 어휘 마스터",
         career="영어교육 석사 · EBS 연계 어휘 분석 10년",
         intro="어원과 예문으로 오래 남는 단어. 독해 속도가 달라집니다."),
    dict(name="이연표", subject="한국사", one_line="흐름으로 외우는 한국사",
         course="수능 한국사 흐름 잡기",
         career="전) 한국사능력검정 최상위 배출 · 한국사 교재 집필",
         intro="사건을 점이 아니라 선으로. 연표가 저절로 그려집니다."),
    dict(name="문해력", subject="국어", one_line="문학, 감상 말고 분석",
         course="수능 국어 문학 분석",
         career="국문학 박사수료 · 문학 파트 전문 10년",
         intro="화자·정서·표현을 도구로. 처음 보는 작품도 뚫립니다."),
    dict(name="그래머", subject="영어", one_line="구문이 보이면 독해가 빨라진다",
         course="수능 영어 구문·문법",
         career="영어학 전공 · 구문 독해 교재 집필",
         intro="문장 구조를 눈으로. 어려운 지문일수록 구문이 답입니다."),
    dict(name="사탐킹", subject="사회탐구", one_line="생윤·사문 개념 압축",
         course="사회탐구 핵심 개념(생윤·사문)",
         career="전) 사탐 대표강사 · 개념 요약서 베스트셀러",
         intro="방대한 사탐을 표 한 장으로. 헷갈리는 개념만 콕콕."),
    dict(name="물화생", subject="과학탐구", one_line="과탐 킬러 유형 정복",
         course="과학탐구 킬러 유형 분석",
         career="과학교육 석사 · 과탐 문항 분석 8년",
         intro="유형을 알면 킬러도 패턴입니다. 실전 순서대로 훈련."),
    dict(name="문학소녀", subject="국어", one_line="화법과 작문, 감점 없이",
         course="수능 국어 화법과 작문",
         career="전) EBS 국어 검토위원 · 화작 파트 전문",
         intro="화작은 실수 싸움입니다. 틀리는 지점만 골라 잡아드려요."),
]


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
        # 스키마 방어: quiz_question.difficulty 가 있는 RDS/로컬에서만 난이도 심음(없으면 생략).
        self._qq_difficulty = self._has_column("quiz_question", "difficulty")
        self._lesson_secs = {}   # lesson_id -> 실제 영상 길이(초). duration_seconds와 동일하게 유지.

    def _has_column(self, table, col):
        self.cur.execute(f"SHOW COLUMNS FROM {table} LIKE %s", (col,))
        return self.cur.fetchone() is not None

    def x(self, sql, args=None):
        self.cur.execute(sql, args or ())
        return self.cur.lastrowid

    def q1(self, sql, args=None):
        self.cur.execute(sql, args or ())
        return self.cur.fetchone()

    # ── 재실행 대비 데모 데이터 삭제 ──
    def wipe(self):
        instructor_ids = [INSTRUCTOR_ID] + CATALOG_INSTRUCTOR_IDS
        ids = ",".join(str(i) for i in PERSONA_IDS + instructor_ids)
        self.cur.execute("SET FOREIGN_KEY_CHECKS=0")
        # 전역/알림: 데모 DB라 fsrs_params 전체 초기화, 페르소나 알림 정리
        self.cur.execute("DELETE FROM fsrs_params")
        self.cur.execute(f"DELETE FROM notification WHERE receiver_id IN ({ids})")
        # 코스: 데모 강사 + 카탈로그 강사 10명이 만든 것 전부
        iph = ",".join(str(i) for i in instructor_ids)
        self.cur.execute(f"SELECT course_id FROM course WHERE author_id IN ({iph})")
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
            for t in ("daily_achievement", "dropout_risk", "dropout_event", "enrollment_onboarding"):
                del_in(t, "enrollment_id", enr_ids)
        # member 기준
        # student_availability: V3.2.3 에서 enrollment_id -> member_id 로 전환됨(학생 단위).
        # 예전엔 enrollment_id 로 지웠는데, 첫 시딩은 enr_ids 가 비어 이 블록을 건너뛰어서
        # 드러나지 않았고 재실행(멱등) 때만 1054 Unknown column 으로 터졌다.
        # video_progress: video_id=lesson.id 인데 재시딩 때 lesson id가 바뀌므로 같이 지워야
        # 고아 행이 안 남는다(잔디 daily_study_stats 는 시더 무관 — 건드리지 않음).
        for t, col in (("enrollment", "member_id"), ("member_lesson_stat", "member_id"),
                       ("student_availability", "member_id"),
                       ("student_capacity", "student_id"), ("student_diagnostic_score", "member_id"),
                       ("quiz_submission", "member_id"), ("video_progress", "member_id")):
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

    def _lesson(self, order, title, section_id):
        """재생 영상 붙은 lesson 1개. duration_seconds는 실제 영상 길이(DEMO_VIDEOS)로 맞춘다 —
        효율계수 계산은 이 값이 아니라 EXPECTED_MIN 상수를 직접 쓰므로 서로 영향 없음."""
        vurl, secs = DEMO_VIDEOS[(order - 1) % len(DEMO_VIDEOS)]
        lid = self.x("""INSERT INTO lesson
            (created_at, order_index, title, section_id, duration_seconds, video_url, file_processing_status)
            VALUES (%s,%s,%s,%s,%s,%s,'COMPLETED')""",
            (dt_str(TODAY_DT), order, title, section_id, secs, vurl))
        self._lesson_secs[lid] = secs
        return lid

    def _quiz_generic(self, qid, idx):
        """가짜 퀴즈(국어·탐구): 문항 3 × 보기 4, 1번 정답. 반환 qmeta=[(question_id,[option_id..])]."""
        qmeta = []
        for qn in range(1, 4):
            qqid = self.x("INSERT INTO quiz_question (quiz_id, question_number, question_text) VALUES (%s,%s,%s)",
                         (qid, qn, f"{idx}강 {qn}번 문제"))
            opts = [self.x("INSERT INTO quiz_option (question_id, option_number, option_text, is_correct) VALUES (%s,%s,%s,%s)",
                           (qqid, on, f"보기{on}", 1 if on == 1 else 0)) for on in range(1, 5)]
            qmeta.append((qqid, opts))
        return qmeta

    def _quiz_from_bank(self, qid, questions):
        """윤종호 실문제 퀴즈: (문제,정답,난이도) 리스트 → 문항+보기. 1번=정답,
        오답 보기는 같은 섹션 다른 정답으로 채움(원본 distractor 부재). 반환 qmeta."""
        pool = list(dict.fromkeys(a for (_, a, _) in questions))   # 섹션 내 유니크 정답
        qmeta = []
        for qn, (prob, ans, diff) in enumerate(questions, 1):
            if self._qq_difficulty:
                qqid = self.x("INSERT INTO quiz_question (quiz_id, question_number, question_text, difficulty) VALUES (%s,%s,%s,%s)",
                             (qid, qn, prob, diff))
            else:
                qqid = self.x("INSERT INTO quiz_question (quiz_id, question_number, question_text) VALUES (%s,%s,%s)",
                             (qid, qn, prob))
            distractors = [a for a in pool if a != ans][:3]
            while len(distractors) < 3:
                distractors.append(f"보기{len(distractors) + 2}")
            texts = [ans] + distractors                             # option_number 1 = 정답
            opts = [self.x("INSERT INTO quiz_option (question_id, option_number, option_text, is_correct) VALUES (%s,%s,%s,%s)",
                           (qqid, on, t, 1 if on == 1 else 0)) for on, t in enumerate(texts, 1)]
            qmeta.append((qqid, opts))
        return qmeta

    def _build_course(self, spec):
        """코스 1개 = 강의 N + 선수관계 + 정책 + 강의당 퀴즈1. bank 있으면 섹션=강의로 실문제 심음."""
        thumbnail = _s3_thumb(DEMO_THUMB_KEYS.get(spec["key"])) or spec["thumb"]
        course_id = self.x("""INSERT INTO course
            (author_id, price, title, created_at, description, price_type, status, subject, thumbnail_url)
            VALUES (%s, 0, %s, %s, %s, 'FREE', 'PUBLISHED', %s, %s)""",
            (INSTRUCTOR_ID, spec["title"], dt_str(TODAY_DT), "데모용 코스", spec["subject"], thumbnail))
        lessons = []
        quiz_by_lesson = {}
        if spec.get("bank"):
            # 섹션(=단원) 하나 = 강의 1 + 퀴즈 1(문항 10). course_section.title = 단원명.
            for si, sec in enumerate(MATH_QUIZ_BANK[spec["bank"]]["sections"], 1):
                section_id = self.x("INSERT INTO course_section (order_index, title, course_id) VALUES (%s,%s,%s)",
                                    (si, sec["title"], course_id))
                lid = self._lesson(si, sec["title"], section_id)
                lessons.append(lid)
                qid = self.x("INSERT INTO quiz (course_id, section_id, instructor_id, title) VALUES (%s,%s,%s,%s)",
                            (course_id, section_id, INSTRUCTOR_ID, f"{sec['title']} 퀴즈"))
                self.x("INSERT INTO lesson_quiz_map (lesson_id, quiz_id) VALUES (%s,%s)", (lid, qid))
                quiz_by_lesson[lid] = (qid, self._quiz_from_bank(qid, sec["questions"]))
        else:
            section_id = self.x("INSERT INTO course_section (order_index, title, course_id) VALUES (1,%s,%s)",
                                ("전체", course_id))
            for i in range(1, spec["lessons"] + 1):
                lid = self._lesson(i, f"{i}강", section_id)
                lessons.append(lid)
                qid = self.x("INSERT INTO quiz (course_id, section_id, instructor_id, title) VALUES (%s,%s,%s,%s)",
                            (course_id, section_id, INSTRUCTOR_ID, f"{i}강 퀴즈"))
                self.x("INSERT INTO lesson_quiz_map (lesson_id, quiz_id) VALUES (%s,%s)", (lid, qid))
                quiz_by_lesson[lid] = (qid, self._quiz_generic(qid, i))
        # 선수관계: 순차(각 강의는 직전 강의 선수) - CP-SAT 순서 제약 시연
        for a, b in zip(lessons, lessons[1:]):
            self.x("INSERT INTO lesson_prerequisite (lesson_id, prerequisite_lesson_id) VALUES (%s,%s)", (b, a))
        # 코스 정책 (코스별 권장 완강 주수·하루권장·난이도)
        self.x("""INSERT INTO course_learning_policy
            (course_id, recommended_duration_weeks, daily_recommended_minutes, difficulty, weekly_max_load_min)
            VALUES (%s, %s, %s, %s, %s)""",
            (course_id, spec["weeks"], spec["daily"], spec["difficulty"], spec["weekly_max"]))
        return dict(spec=spec, course_id=course_id, lessons=lessons, quiz_by_lesson=quiz_by_lesson)

    def fixtures(self):
        """강사 + 데모 코스 3개(각 lesson N·선수관계·정책·퀴즈) + fsrs global.
        반환: {course_key: fixture dict}."""
        self.member(INSTRUCTOR_ID, "김강사데모", "demo_inst", "INSTRUCTOR")
        courses = {spec["key"]: self._build_course(spec) for spec in DEMO_COURSES}
        # FSRS global params (전역 1행)
        try:
            from fsrs import Scheduler
            weights = list(getattr(Scheduler(), "parameters", []) or [])
        except Exception:
            weights = []
        self.x("""INSERT INTO fsrs_params (scope, student_id, weights, retention_target)
            VALUES ('GLOBAL', NULL, %s, 0.9)""", (json.dumps(weights),))
        return courses

    def catalog(self):
        """둘러보기 화면용 강사 10명 + 코스 10개 + 코스당 재생되는 영상 강의 4개.
        학생이 수강하지 않는 카탈로그 데이터(스케줄러/위험도 서사와 무관)."""
        for idx, c in enumerate(CATALOG_INSTRUCTORS):
            mid = CATALOG_INSTRUCTOR_IDS[idx]
            uname = f"inst_cat{idx + 1}"
            # 강사(설명 포함): career=경력, introduction=소개, one_line_intro=한 줄 소개
            self.x("""INSERT INTO members
                (member_id, name, email, username, password, role, status,
                 is_locked, is_password_change_required, login_fail_count, optional_terms_agreed,
                 career, introduction, one_line_intro, created_at)
                VALUES (%s,%s,%s,%s,%s,'INSTRUCTOR','ACTIVE', 0,0,0,1, %s,%s,%s,%s)""",
                (mid, c["name"], f"{uname}@flown.demo", uname, PW_HASH,
                 c["career"], c["intro"], c["one_line"], dt_str(TODAY_DT)))
            cthumb = (_s3_thumb(CATALOG_THUMB_KEYS[idx % len(CATALOG_THUMB_KEYS)]) if CATALOG_THUMB_KEYS else None) or CATALOG_THUMBS[idx]
            course_id = self.x("""INSERT INTO course
                (author_id, price, title, created_at, description, price_type, status, subject, thumbnail_url)
                VALUES (%s, 0, %s, %s, %s, 'FREE', 'PUBLISHED', %s, %s)""",
                (mid, c["course"], dt_str(TODAY_DT),
                 f"{c['name']} 강사의 {c['course']}. {c['intro']}", c["subject"], cthumb))
            section_id = self.x("INSERT INTO course_section (order_index, title, course_id) VALUES (1,%s,%s)",
                                ("전체", course_id))
            for li in range(1, 5):   # 강의 4개, 재생되는 영상 부여
                url, secs = DEMO_VIDEOS[(li - 1) % len(DEMO_VIDEOS)]
                self.x("""INSERT INTO lesson
                    (created_at, order_index, title, section_id, duration_seconds, video_url, file_processing_status)
                    VALUES (%s,%s,%s,%s,%s,%s,'COMPLETED')""",
                    (dt_str(TODAY_DT), li, f"{li}강", section_id, secs, url))
            # 코스 정책(둘러보기/상세에서 참조) — 데모 코스와 동일 형식
            self.x("""INSERT INTO course_learning_policy
                (course_id, recommended_duration_weeks, daily_recommended_minutes, difficulty, weekly_max_load_min)
                VALUES (%s, 8, 90, 'MEDIUM', 630)""", (course_id,))

    def persona(self, mid, cfg, courses):
        """학생 1명: 계정·capacity·availability(학생 단위 1회) + 수강 코스별 enrollment 루프.
        이탈위험/활동이력은 primary(국어) enrollment에만 심어 관리자 목록이 학생당 1줄로 뜨게 한다."""
        self.member(mid, cfg["name"], cfg["username"], "STUDENT")
        enrolled_at = TODAY - timedelta(days=cfg["enrolled_ago"])
        enrolled_dt = dt_str(datetime.combine(enrolled_at, datetime.min.time()))
        study_days = max(1, 7 - popcount(cfg["rest_days"]))
        weekly_avail_total = cfg["daily_cap"] * study_days
        monday = TODAY - timedelta(days=TODAY.weekday())

        # ── 학생 단위(코스 무관) 1회 ──
        # V3.2.4: rest_days·onboarded_at는 student_capacity(학생 단위). onboarded_at 채워 온보딩 완료로 인식.
        self.x("INSERT INTO student_capacity (student_id, daily_cap_min, rest_days, onboarded_at) VALUES (%s,%s,%s,%s)",
               (mid, cfg["daily_cap"], cfg["rest_days"], enrolled_dt))
        # 가용시간: 학습일마다 저녁 19-22시. V3.2.3: student_availability는 member_id(학생 단위).
        for dow in range(7):
            if not (cfg["rest_days"] >> dow) & 1:
                self.x("INSERT INTO student_availability (member_id, day_of_week, start_time, end_time) VALUES (%s,%s,'19:00:00','22:00:00')",
                       (mid, dow))

        # ── 수강 코스별 enrollment (completed 의 키 = 수강 코스) ──
        my_courses = [courses[spec["key"]] for spec in DEMO_COURSES if spec["key"] in cfg["completed"]]
        primary_enr = None
        all_quiz = []
        summary = None
        for course in my_courses:
            res = self._seed_enrollment(mid, cfg, course, enrolled_at, enrolled_dt,
                                        study_days, weekly_avail_total, monday)
            all_quiz.extend(res["quiz_scores"])
            if summary is None or course["spec"]["key"] == PRIMARY_COURSE_KEY:
                primary_enr = res["enrollment_id"]   # 국어 우선, 없으면 첫 코스
                summary = res

        avg_quiz = sum(all_quiz) / len(all_quiz) if all_quiz else None

        # ── daily_achievement 백필 (primary enrollment 1개에만) ──
        daily_planned = cfg["daily_cap"]
        for d in range(cfg["enrolled_ago"], 0, -1):
            adate = TODAY - timedelta(days=d)
            in_streak = d <= cfg["miss_streak"]                       # 마지막 streak일 미달
            achieved = 0 if in_streak else (0 if (d % 7 == 0) else 1)  # 쉬는날 근사 미달
            if cfg["dropout"] and d <= cfg["recency"]:
                achieved = 0
            actual = 0 if achieved == 0 else daily_planned
            self.x("""INSERT INTO daily_achievement
                (enrollment_id, achieved_date, planned_min, actual_min, achieved)
                VALUES (%s,%s,%s,%s,%s)""",
                (primary_enr, dt_str(adate)[:10], daily_planned, actual, achieved))

        # ── 이탈위험 (규칙기반) — primary enrollment 1개에만 ──
        breakdown = compute_risk_breakdown(cfg["recency"], cfg["miss_streak"], avg_quiz)
        self.x("""INSERT INTO dropout_risk
            (enrollment_id, computed_at, risk_score, method, recency_days, miss_streak, features)
            VALUES (%s,%s,%s,'RULE',%s,%s,%s)""",
            (primary_enr, dt_str(TODAY_DT), breakdown.score, cfg["recency"], cfg["miss_streak"],
             json.dumps({"label": breakdown.label, "top_reason": breakdown.top_reason,
                         "contributions": breakdown.contributions}, ensure_ascii=False)))
        if cfg["dropout"]:
            self.x("""INSERT INTO dropout_event (enrollment_id, event_occurred, event_date, censored, observed_days)
                VALUES (%s,1,%s,0,%s)""",
                (primary_enr, dt_str(TODAY - timedelta(days=cfg["recency"]))[:10], cfg["enrolled_ago"]))
        else:
            self.x("""INSERT INTO dropout_event (enrollment_id, event_occurred, event_date, censored, observed_days)
                VALUES (%s,0,NULL,1,%s)""", (primary_enr, cfg["enrolled_ago"]))

        return dict(name=cfg["name"], courses=len(my_courses), coeff=summary["coeff"],
                    num_weeks=summary["num_weeks"], ext=summary["ext"],
                    weekly_avail=weekly_avail_total, remaining=summary["remaining"],
                    avg_quiz=round(avg_quiz, 1) if avg_quiz else None,
                    risk=breakdown.score, risk_label=breakdown.label, top_reason=breakdown.top_reason,
                    status=summary["status"])

    def _seed_enrollment(self, mid, cfg, course, enrolled_at, enrolled_dt,
                         study_days, weekly_avail_total, monday):
        """한 코스 수강분: enrollment·진단·완료실측·퀴즈·복습·스케줄. 반환 요약 dict."""
        spec = course["spec"]
        course_id = course["course_id"]
        lessons = course["lessons"]
        quiz_by_lesson = course["quiz_by_lesson"]
        start_time = spec["start_time"]
        n_done = cfg["completed"][spec["key"]]

        enrollment_id = self.x("""INSERT INTO enrollment
            (course_id, enrolled_at, member_id, status, target_weeks, target_weeks_original)
            VALUES (%s,%s,%s,'IN_PROGRESS',%s,%s)""",
            (course_id, enrolled_dt, mid, cfg["target_weeks"], cfg["target_weeks"]))
        self.x("INSERT INTO enrollment_onboarding (enrollment_id, rest_days, onboarded_at) VALUES (%s,%s,%s)",
               (enrollment_id, cfg["rest_days"], enrolled_dt))
        # 진단 등급은 코스별(전 페르소나 동일) — 수학이 취약(5등급)이라 CP-SAT 가중치가 커진다.
        self.x("INSERT INTO student_diagnostic_score (member_id, course_id, grade, exam_date) VALUES (%s,%s,%s,%s)",
               (mid, course_id, spec["grade"], dt_str(TODAY - timedelta(days=30))[:10]))

        completed = lessons[:n_done]
        remaining = lessons[n_done:]

        # ── member_lesson_stat (완료 강의 실측) ──
        for lid in completed:
            self.x("""INSERT INTO member_lesson_stat
                (member_id, lesson_id, actual_completion_sec, rewatch_count, last_studied_at)
                VALUES (%s,%s,%s,%s,%s)""",
                (mid, lid, cfg["actual_sec"], 2 if cfg["actual_sec"] > EXPECTED_SEC else 0,
                 dt_str(datetime.combine(TODAY - timedelta(days=cfg["recency"]), datetime.min.time()))))

        # ── video_progress (완료 강의 시청완료 백필) ──
        # 학생 화면 진도율(learning_activity GetCourseProgress)은 video_progress.is_completed
        # (video_id = lesson.id) 만으로 계산된다 — 이 백필이 없으면 페르소나 진도율이 전부 0%.
        # 완료 일시는 과거 DONE 슬롯과 같은 날짜(저녁 21시)로 맞춘다.
        for i, lid in enumerate(completed):
            vdt = datetime.combine(monday - timedelta(days=len(completed) - i),
                                   datetime.min.time()) + timedelta(hours=21)
            lesson_sec = self._lesson_secs.get(lid, EXPECTED_SEC)
            self.x("""INSERT INTO video_progress
                (member_id, course_id, video_id, last_position_sec, watch_time_sec,
                 is_completed, completed_at, updated_at)
                VALUES (%s,%s,%s,%s,%s,1,%s,%s)""",
                (mid, course_id, lid, lesson_sec, lesson_sec, dt_str(vdt), dt_str(vdt)))

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

            # FSRS: 카드당 여러 번 복습(aggregate 16+ 로그 목표). placeholder 카드 생성 후 로그를 붙이고 최종 UPDATE.
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

        # ── 스케줄 (CP-SAT) ── 주간가용시간을 코스별 avail_frac 로 쪼갠다(취약 수학에 더 배분). ──
        num_weeks = compute_num_weeks(TODAY, enrolled_at, cfg["target_weeks"], None)
        adjusted = max(1, round(EXPECTED_MIN * coeff))
        weekly_avail = max(adjusted, int(weekly_avail_total * spec["avail_frac"]))

        # ── 완료분을 과거 DONE 슬롯으로 백필 ── (forward엔 remaining만 → progressRate가 페르소나별로 갈리게)
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
                    VALUES (%s,%s,%s,%s,%s,'DONE')""",
                    (past_ws_id, lid, dt_str(done_date)[:10], start_time, adjusted))
        cpsat_lessons = [{"id": lid, "course_id": course_id, "duration_min": adjusted,
                          "deadline_week": num_weeks - 1} for lid in remaining]
        prereqs = [(a, b) for a, b in zip(lessons, lessons[1:]) if a in remaining and b in remaining]
        caps = [weekly_avail] * num_weeks
        assignment = generate_unified_weekly_schedule(cpsat_lessons, caps, prereqs, {course_id: spec["grade"]})
        ext = 0
        if assignment is None and remaining:
            totals = [{"total_duration_min": adjusted * len(remaining), "deadline_week": num_weeks - 1}]
            ext = compute_required_extension_weeks(totals, weekly_avail, SLIP_BUFFER_WEEKS)
            if ext > 0:
                caps = [weekly_avail] * (num_weeks + ext)
                for l in cpsat_lessons:
                    l["deadline_week"] = num_weeks + ext - 1
                assignment = generate_unified_weekly_schedule(cpsat_lessons, caps, prereqs, {course_id: spec["grade"]})

        status = "OK"
        if assignment is None:
            status = "INFEASIBLE"
            self.x("""INSERT INTO notification (created_at, is_read, message, receiver_id, type)
                VALUES (%s,0,%s,%s,'NOTICE')""",
                (dt_str(TODAY_DT), f"[{spec['subject']}] 지금 설정으로는 목표기간 내 완주가 어려워요. 목표기간이나 학습량을 조정해주세요.", mid))
        else:
            total_weeks = num_weeks + ext
            reason = f"[{spec['subject']}] 초기 생성 (효율계수 {coeff:.2f}, {total_weeks}주 배정" + (f", +{ext}주 연장)" if ext else ")")
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
                        VALUES (%s,%s,%s,%s,%s,'PLANNED')""",
                        (ws_id, lid, dt_str(plan_date)[:10], start_time, adjusted))

        return dict(enrollment_id=enrollment_id, coeff=round(coeff, 2), num_weeks=num_weeks,
                    ext=ext, remaining=len(remaining), quiz_scores=quiz_scores_used, status=status)


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
    print(f"seeding fixtures ({len(DEMO_COURSES)} 데모 코스)…")
    courses = s.fixtures()
    print(f"seeding catalog ({len(CATALOG_INSTRUCTORS)} instructors + courses + videos)…")
    s.catalog()
    results = []
    for mid, cfg in PERSONAS.items():
        print(f"seeding {cfg['name']} ({mid})…")
        results.append(s.persona(mid, cfg, courses))
    conn.commit()

    print("\n=== 페르소나 요약 (스케줄/위험은 국어 primary 기준) ===")
    hdr = f"{'페르소나':<8}{'코스':>4}{'효율':>6}{'주수':>5}{'연장':>5}{'주간가용':>8}{'남은강의':>8}{'평균퀴즈':>8}{'위험':>7}{'라벨':>8}{'사유':>10}{'스케줄':>10}"
    print(hdr)
    for r in results:
        print(f"{r['name']:<8}{r['courses']:>4}{r['coeff']:>6}{r['num_weeks']:>5}{r['ext']:>5}{r['weekly_avail']:>8}"
              f"{r['remaining']:>8}{str(r['avg_quiz']):>8}{r['risk']:>7}{r['risk_label']:>8}{r['top_reason']:>10}{r['status']:>10}")
    conn.close()


if __name__ == "__main__":
    main()
