"""발표 데모용 **추가 콘텐츠 시더** (강사·강의·리뷰·커뮤니티·스터디/실시간채팅).

seed_demo.py 와 완전히 분리된 독립 스크립트다. 목적:
  "실제 운영 중인 사이트"처럼 카탈로그/커뮤니티가 차 있게 만들되,
  **페르소나 시드(members 9200 / 9201~9214 / 9220~9229)와 기존 데이터는 절대 건드리지 않는다.**

그래서 seed_demo.py 를 수정/재실행하지 않는다(그건 매 실행 시 페르소나 포함 전체를
wipe·재생성하므로). 대신 아래 신규 ID 대역만 쓰고, wipe_content()도 이 대역만 지운다.

  - 신규 강사  : 9240~9259  (대표 과목별 강사)
  - 신규 학생풀: 9300~9349  (커뮤니티 글/댓글/리뷰/스터디/채팅 작성자 — 페르소나 아님)

핵심 스키마 사실(코드 대조로 확인):
  - 과목 필터: CourseRepositoryAdapter 가 course.subject 를 SubjectType enum명과 정확일치
    비교(cb.equal). 그래서 course.subject 에 enum명("KO_READING")을 넣어야 필터에 잡힌다.
  - course.level = 한글 라벨("입문/중급/심화").
  - thumbnail_url/profile_image_url: http(s):// 로 시작하면 S3UrlPresigner 가 그대로 통과.
  - study 목록은 DISSOLVED 제외 → 상태는 ACTIVE/FULL 만 사용. study.subject = enum코드.
  - posts 목록은 board_type + status=ACTIVE 로 조회. 질문글 is_accepted 로 채택완료/대기 배지.
  - 리뷰는 course_id 기준 집계 → reviewCount>0 이어야 별점 노출.

실행 (Python-Server 디렉토리):
  # 1) 먼저 썸네일 업로드해서 scripts/demo_content_thumbnails.py 생성 (S3 렌더용)
  S3_BUCKET=<버킷> python -m scripts.upload_demo_thumbs
  # 2) 콘텐츠 시드 (프로덕션 RDS)
  DB_HOST=<RDS> DB_USER=... DB_PASSWORD=... DB_NAME=Hard_Click S3_BUCKET=<버킷> \
    python -m scripts.seed_content
  # 로컬 선검증: DB_NAME=hardclick_db, S3_BUCKET 없으면 썸네일은 picsum 폴백.
"""
import os
import random
import sys
from datetime import date, datetime, timedelta, timezone

import pymysql
from pymysql.cursors import DictCursor

# ── seed_demo 와 동일 값(의존성 없이 재선언; 단일 출처는 seed_demo.py) ──
PW_HASH = "$2a$10$jyOD5ilYEfVfy2U91g5kUelb.eqh36wqv/yx928qAywPgZURHpzI."  # Flown2026!
# 실제 재생되는 공개 영상(archive.org, 임베드/핫링크 가능). (url, 길이초). s3_key 없으면 video_url 직접 스트리밍.
DEMO_VIDEOS = [
    ("https://archive.org/download/BigBuckBunny_124/Content/big_buck_bunny_720p_surround.mp4", 596),
    ("https://archive.org/download/Sintel/sintel-2048-surround_512kb.mp4", 888),
    ("https://archive.org/download/ElephantsDream/ed_1024_512kb.mp4", 654),
]

_SEED_TODAY = os.environ.get("SEED_TODAY")
TODAY = date.fromisoformat(_SEED_TODAY) if _SEED_TODAY else date.today()
TODAY_DT = datetime(TODAY.year, TODAY.month, TODAY.day, 9, 0, tzinfo=timezone.utc)

# ── 신규 ID 대역 (기존/페르소나와 절대 안 겹침) ──
INSTRUCTOR_IDS = list(range(9240, 9260))   # 실제 사용분은 SUBJECTS 길이만큼
STUDENT_IDS = list(range(9300, 9350))
REGION = os.environ.get("AWS_DEFAULT_REGION", "ap-northeast-2")

# ── 썸네일 키 매핑 (upload_demo_thumbs.py 가 생성) ──
try:
    from scripts.demo_content_thumbnails import S3_PREFIX, THUMB_KEYS
except Exception:
    try:
        from demo_content_thumbnails import S3_PREFIX, THUMB_KEYS
    except Exception:
        S3_PREFIX, THUMB_KEYS = "thumbnails/demo-content", []
_S3_BUCKET = os.environ.get("S3_BUCKET")


def thumb_url(i):
    """i번째 썸네일 public URL. S3 버킷+키 있으면 S3, 없으면 picsum 폴백(둘 다 <Image> 허용 호스트)."""
    if _S3_BUCKET and THUMB_KEYS:
        key = THUMB_KEYS[i % len(THUMB_KEYS)]
        return f"https://{_S3_BUCKET}.s3.{REGION}.amazonaws.com/{S3_PREFIX}/{key}"
    return f"https://picsum.photos/seed/flown{i}/800/450"


def dt_str(d):
    if isinstance(d, datetime):
        d = d.astimezone(timezone.utc).replace(tzinfo=None) if d.tzinfo else d
        return d.strftime("%Y-%m-%d %H:%M:%S")
    return d.strftime("%Y-%m-%d %H:%M:%S")


# ── 대표 과목별 강사+강의 (7개 대분류 커버, subject=SubjectType enum명) ──
SUBJECTS = [
    dict(code="KO_READING", label="독서", price=0, level="중급",
         iname="한지문", ione="비문학 독해 12년, 지문이 눈에 들어오게",
         icareer="전) 대형학원 국어 대표강사\n수능 비문학 교재 3종 집필\n국어교육과 졸업",
         iintro="지문을 '읽는 법'부터 다시 잡습니다. 감이 아니라 근거로 답을 고르게.",
         title="수능 국어 독서(비문학) 독해 전략",
         obj=["지문 구조를 근거로 읽는 독해 원리 확립", "선택지 소거법과 빈출 함정 대비", "실전 시간 배분 훈련"],
         aud=["국어 3~4등급에서 2등급을 목표하는 수험생", "감으로 풀어 편차가 큰 학생"],
         tags=["비문학", "독해", "수능국어"], lessons=6),
    dict(code="KO_LITERATURE", label="문학", price=55000, level="중급",
         iname="문해력", ione="문학, 감상 말고 분석",
         icareer="국문학 박사수료\n문학 파트 전문 10년\nEBS 문학 검토위원",
         iintro="화자·정서·표현을 도구로. 처음 보는 작품도 뚫립니다.",
         title="수능 국어 문학 분석 마스터",
         obj=["운문·산문 감상의 분석틀 확립", "표현법·서술상 특징 정리", "낯선 작품 대응 훈련"],
         aud=["문학에서 실수가 잦은 수험생", "작품 해석이 들쭉날쭉한 학생"],
         tags=["문학", "현대시", "고전"], lessons=6),
    dict(code="MATH_1", label="수학Ⅰ", price=89000, level="심화",
         iname="정석해", ione="개념의 '왜'부터, 킬러까지",
         icareer="전) 메가스터디 수학 강사\n수학교육과 졸업\n수능 수학 교재 다수 집필",
         iintro="개념을 도구로 바꿔 드립니다. 기출에 바로 적용되게.",
         title="수학Ⅰ 개념+기출 완성",
         obj=["지수·로그·삼각함수 개념 완성", "수열 계산 감각 확립", "킬러 문항 접근 전략"],
         aud=["수학 4~5등급에서 개념을 메우려는 수험생", "기출 적용이 약한 학생"],
         tags=["지수로그", "삼각함수", "수열"], lessons=6),
    dict(code="MATH_CALCULUS", label="미적분", price=89000, level="심화",
         iname="미분값", ione="미적분, 계산이 아니라 흐름",
         icareer="수학과 졸업\n미적분 전문 9년\n모의고사 검토위원",
         iintro="극한→미분→적분의 흐름을 한 줄기로. 실전 속도가 달라집니다.",
         title="수능 미적분 실전 특강",
         obj=["극한·연속의 엄밀한 이해", "미분·적분 계산 자동화", "그래프/넓이 킬러 대비"],
         aud=["미적분을 선택한 수험생", "계산 실수가 잦은 학생"],
         tags=["극한", "미분", "적분"], lessons=6),
    dict(code="ENG_1", label="영어Ⅰ", price=0, level="입문",
         iname="김보카", ione="하루 30단어, 수능 어휘 정복",
         icareer="영어교육 석사\nEBS 연계 어휘 분석 10년",
         iintro="어원과 예문으로 오래 남는 단어. 독해 속도가 달라집니다.",
         title="수능 영어 어휘·구문 기초",
         obj=["빈출 어휘 어원 학습", "핵심 구문 독해", "듣기 기초 다지기"],
         aud=["영어 기초를 다지려는 수험생", "어휘가 약한 학생"],
         tags=["어휘", "구문", "수능영어"], lessons=6),
    dict(code="KOR_HISTORY", label="한국사", price=0, level="입문",
         iname="이연표", ione="흐름으로 외우는 한국사",
         icareer="전) 한국사능력검정 최상위 배출\n한국사 교재 집필",
         iintro="사건을 점이 아니라 선으로. 연표가 저절로 그려집니다.",
         title="수능 한국사 흐름 잡기",
         obj=["시대별 핵심 흐름 정리", "빈출 사료 해석", "필수 개념 압축"],
         aud=["한국사를 빠르게 훑고 싶은 수험생", "암기가 막막한 학생"],
         tags=["한국사", "흐름", "사료"], lessons=5),
    dict(code="SO_LIFE_ETHICS", label="생활과 윤리", price=45000, level="중급",
         iname="사탐킹", ione="생윤 개념, 표 한 장으로",
         icareer="전) 사탐 대표강사\n개념 요약서 베스트셀러",
         iintro="방대한 사탐을 표 한 장으로. 헷갈리는 개념만 콕콕.",
         title="생활과 윤리 핵심 개념",
         obj=["사상가별 핵심 주장 정리", "빈출 지문 유형 대비", "선지 오답 관리"],
         aud=["생윤 개념을 압축하려는 수험생", "선택과목 입문자"],
         tags=["생윤", "윤리", "사탐"], lessons=5),
    dict(code="SO_CULTURE", label="사회·문화", price=45000, level="중급",
         iname="자료해석", ione="사문 자료·도표, 실수 없이",
         icareer="사회교육과 졸업\n사문 자료분석 전문",
         iintro="사문은 자료 싸움입니다. 도표에서 답을 뽑는 법.",
         title="사회·문화 자료 분석 특강",
         obj=["연구방법 핵심 정리", "도표·통계 해석 훈련", "빈출 함정 대비"],
         aud=["사문 자료문제가 약한 수험생", "개념은 되는데 도표가 막히는 학생"],
         tags=["사회문화", "도표", "연구방법"], lessons=5),
    dict(code="SC_PHYSICS_1", label="물리학Ⅰ", price=69000, level="심화",
         iname="물화생", ione="과탐 킬러 유형 정복",
         icareer="과학교육 석사\n과탐 문항 분석 8년",
         iintro="유형을 알면 킬러도 패턴입니다. 실전 순서대로 훈련.",
         title="물리학Ⅰ 개념·킬러 유형",
         obj=["역학 핵심 개념 완성", "전자기 빈출 유형", "킬러 계산 전략"],
         aud=["물리Ⅰ 선택 수험생", "역학이 약한 학생"],
         tags=["역학", "전자기", "물리"], lessons=6),
    dict(code="SC_BIOLOGY_1", label="생명과학Ⅰ", price=69000, level="중급",
         iname="생명톡", ione="생윤 아니고 생명, 그림으로",
         icareer="생물교육과 졸업\n생명과학 전문 7년",
         iintro="유전·항상성을 그림으로. 헷갈리는 과정도 한눈에.",
         title="생명과학Ⅰ 개념 완성",
         obj=["세포·유전 핵심 정리", "항상성 과정 이해", "빈출 실험 해석"],
         aud=["생명Ⅰ 선택 수험생", "유전 문제가 약한 학생"],
         tags=["유전", "항상성", "생명"], lessons=6),
    dict(code="FL_JAPANESE", label="일본어Ⅰ", price=0, level="입문",
         iname="사쿠라", ione="히라가나부터 수능까지",
         icareer="일어일문학과 졸업\n제2외국어 강의 6년",
         iintro="기초 문자부터 실전 회화까지. 제2외국어 만점 전략.",
         title="수능 일본어Ⅰ 기초완성",
         obj=["문자·발음 기초", "필수 문형 정리", "빈출 회화 표현"],
         aud=["일본어를 처음 시작하는 수험생", "제2외국어 선택자"],
         tags=["일본어", "히라가나", "제2외국어"], lessons=5),
    dict(code="FL_CHINESE", label="중국어Ⅰ", price=0, level="입문",
         iname="니하오", ione="병음부터 차근차근",
         icareer="중어중문학과 졸업\n중국어 강의 6년",
         iintro="성조·병음 기초부터. 제2외국어 안정 등급 만들기.",
         title="수능 중국어Ⅰ 기초완성",
         obj=["병음·성조 기초", "핵심 어휘·문형", "빈출 표현 정리"],
         aud=["중국어를 처음 시작하는 수험생", "제2외국어 선택자"],
         tags=["중국어", "병음", "제2외국어"], lessons=5),
]

REVIEW_TEMPLATES = [
    (5, "설명이 군더더기 없이 깔끔해서 이해가 정말 빠릅니다. 강추해요."),
    (5, "개념 잡는 방식이 확실히 달라요. 덕분에 모의고사 점수가 올랐습니다."),
    (4, "전반적으로 아주 만족스럽습니다. 심화 문제가 조금 더 있으면 완벽할 듯해요."),
    (5, "혼자 헤매던 부분이 한 번에 정리됐어요. 커리큘럼이 좋네요."),
    (4, "체계적이라 따라가기 좋습니다. 복습 자료도 알차요."),
    (3, "내용은 좋은데 진도가 살짝 빠른 느낌이 있어요. 반복 시청 추천."),
    (5, "예시가 실전과 딱 맞아서 배운 걸 바로 적용할 수 있었습니다."),
    (4, "기초가 부족했는데 이 강의로 감을 잡았어요. 입문자에게 좋아요."),
    (5, "질문 게시판 답변도 빠르고 친절합니다. 믿고 듣는 강의."),
    (4, "가격 대비 구성이 훌륭합니다. 다음 강의도 결제했어요."),
]

STUDENT_NAMES = [
    "김서준", "이하은", "박도윤", "최지우", "정시우", "강하윤", "조은채", "윤지호",
    "장서连", "임하준", "한소율", "오지안", "서예준", "신유나", "권민재", "황서아",
    "안도현", "송하린", "류지완", "홍아윤", "전우진", "고나은", "문시윤", "배주원",
    "백서연", "허준서", "남다은", "심재하", "노유진", "하태윤",
]

FREE_POSTS = [
    ("수능 D-100 다들 어떻게 보내세요?", "슬슬 마음이 급해지네요. 다들 하루 루틴 어떻게 잡는지 공유해요!"),
    ("스터디카페 vs 독서실 뭐가 나아요?", "집중 잘 되는 환경 찾는 중인데 다들 어디서 공부하시나요?"),
    ("플로운 스케줄러 진짜 편하네요", "밀린 강의 자동 재배치되는 거 신기해요. 잔디 채우는 재미가 있음 ㅋㅋ"),
    ("오답노트 꼭 만들어야 할까요?", "시간이 너무 많이 드는데 효과 보신 분 계신가요?"),
    ("아침형 vs 저녁형 공부", "저는 새벽이 집중이 잘 되는데 다들 어떤 편이세요?"),
    ("모의고사 멘탈 관리 팁 공유", "성적 떨어지면 하루 종일 우울한데 어떻게들 극복하세요?"),
    ("복습 퀴즈 기능 후기", "강의 듣고 바로 퀴즈 푸니까 확실히 기억에 오래 남아요."),
    ("간식 추천 받아요", "밤샘할 때 먹기 좋은 간식 뭐 있을까요? 카페인 말고요!"),
]

# (subject_code, title, content, accepted)
QUESTION_POSTS = [
    ("MATH_1", "로그 부등식 이 문제 풀이 좀 봐주세요", "밑이 1보다 작을 때 부등호 방향을 자꾸 헷갈립니다. 언제 뒤집나요?", True),
    ("KO_READING", "비문학 지문 시간 배분 어떻게 하세요?", "과학지문에서 시간을 너무 많이 씁니다. 노하우 있나요?", True),
    ("MATH_CALCULUS", "치환적분 언제 쓰는지 감이 안 와요", "부분적분이랑 구분이 안 됩니다. 판별 기준이 있을까요?", False),
    ("SC_PHYSICS_1", "등가속도 그래프 해석 질문", "v-t 그래프에서 넓이가 변위인 이유를 직관적으로 알고 싶어요.", True),
    ("ENG_1", "빈칸추론 접근법 질문", "선택지부터 보는 게 나은가요 지문부터가 나은가요?", False),
    ("KO_LITERATURE", "현대시 화자 정서 파악이 어려워요", "표현법은 찾는데 정서를 못 잡겠어요. 훈련법 있나요?", True),
    ("SO_LIFE_ETHICS", "칸트랑 밀 비교 정리 부탁드려요", "의무론/공리주의 선지에서 자꾸 틀립니다.", False),
    ("SC_BIOLOGY_1", "가계도 유전 문제 팁", "우성/열성 판별을 빠르게 하는 순서가 있을까요?", True),
    ("KOR_HISTORY", "근현대사 순서 암기 팁", "사건 순서가 자꾸 섞입니다. 연표 외우는 법 알려주세요.", False),
    ("SO_CULTURE", "일탈이론 도표 문제 질문", "낙인이론이랑 차별교제이론 구분이 헷갈려요.", True),
]

ANSWER_TEMPLATES = [
    "저도 이거 헷갈렸는데, 개념 강의 3강에서 딱 정리해주세요. 한 번 보시면 바로 이해돼요.",
    "핵심만 말하면, 조건을 먼저 확인하고 케이스를 나누는 게 정석이에요.",
    "기출 2021학년도에 비슷한 문제 있어요. 그거 풀어보면 감 잡힙니다.",
    "그림/도표로 정리해두면 시험장에서 안 헷갈려요. 저는 이렇게 외웠어요.",
    "너무 완벽하게 하려 하지 말고 빈출 유형부터 잡으세요. 그게 효율적이에요.",
    "강사님 답변 게시판에 같은 질문 있었어요. 검색해보시면 자세히 나와요.",
]

# (subject_code, title, content, max_count, fill) — fill==max_count 면 모집마감(FULL)
STUDIES = [
    ("MATH_1", "수학Ⅰ 하루 3문제 인증 스터디", "매일 저녁 킬러 3문제 풀이 인증합니다. 노쇼 아웃!", 6, 4),
    ("KO_READING", "비문학 지문 데일리 스터디", "하루 지문 2개 풀고 근거 공유해요. 초보 환영.", 8, 8),
    ("ENG_1", "영어 어휘 30일 챌린지", "하루 30단어 암기 인증. 단톡방에서 퀴즈도 봐요.", 10, 6),
    ("SC_PHYSICS_1", "물리 역학 개념 완성 스터디", "역학 파트 개념+기출 같이 정리합니다. 주 3회.", 5, 5),
    ("MATH_CALCULUS", "미적분 실전 모의 스터디", "주말마다 실전 세트 같이 풀고 리뷰해요.", 6, 3),
    ("SO_LIFE_ETHICS", "생윤 사상가 정리 스터디", "사상가별 주장 표로 정리해서 공유합니다.", 8, 5),
    ("KO_LITERATURE", "문학 작품 분석 스터디", "매일 작품 1개 분석 공유. 낯선 작품 대비.", 6, 6),
    ("KOR_HISTORY", "한국사 흐름 암기 스터디", "시대별 연표 같이 외우고 퀴즈 봐요.", 10, 7),
    ("SC_BIOLOGY_1", "생명과학 유전 집중 스터디", "유전 파트만 파는 단기 스터디입니다.", 5, 2),
    ("SO_CULTURE", "사회문화 도표 특훈", "도표/자료 문제만 골라 풀어요. 실전 위주.", 6, 6),
]

CHAT_LINES = [
    "안녕하세요! 오늘부터 잘 부탁드려요 🙌",
    "다들 오늘 인증 하셨나요?",
    "저 방금 완료했습니다 ㅎㅎ",
    "이 문제 다들 어떻게 푸셨어요?",
    "저는 3번으로 접근했는데 맞나요?",
    "오 그 방법 좋네요 참고할게요!",
    "내일 몇 시에 모일까요?",
    "저녁 9시 어떠세요?",
    "좋아요 그때 봬요 👍",
    "다들 화이팅입니다 🔥",
]

STATUS_ACTIVE = "ACTIVE"


class ContentSeeder:
    def __init__(self, conn):
        self.conn = conn
        self.cur = conn.cursor()
        self.rnd = random.Random(20260727)   # 실행 간 안정(멱등 성격)
        self._chat_has_type = self._has_column("chat_message", "type")

    def _has_column(self, table, col):
        self.cur.execute(f"SHOW COLUMNS FROM {table} LIKE %s", (col,))
        return self.cur.fetchone() is not None

    def x(self, sql, args=None):
        self.cur.execute(sql, args or ())
        return self.cur.lastrowid

    def _in(self, ids):
        return ",".join(str(i) for i in ids)

    def _del_in(self, table, col, vals):
        if vals:
            self.cur.execute(
                f"DELETE FROM {table} WHERE {col} IN ({','.join(['%s'] * len(vals))})", vals)

    # ── 신규 대역만 삭제(멱등). 페르소나/기존 대역은 어떤 조건에도 포함하지 않는다. ──
    def wipe_content(self):
        inst = self._in(INSTRUCTOR_IDS)
        stu = self._in(STUDENT_IDS)
        self.cur.execute("SET FOREIGN_KEY_CHECKS=0")   # FK off → CASCADE 안 걸리므로 자식 명시 삭제

        # 신규 강사가 만든 코스와 그 하위
        self.cur.execute(f"SELECT course_id FROM course WHERE author_id IN ({inst})")
        course_ids = [r["course_id"] for r in self.cur.fetchall()]
        if course_ids:
            self._del_in("reviews", "course_id", course_ids)
            self.cur.execute(
                f"SELECT id FROM course_section WHERE course_id IN ({self._in(course_ids)})")
            sec_ids = [r["id"] for r in self.cur.fetchall()]
            self._del_in("lesson", "section_id", sec_ids)
            self._del_in("course_section", "course_id", course_ids)
            self._del_in("course_learning_policy", "course_id", course_ids)
            self._del_in("course", "course_id", course_ids)
        # 학생풀이 남긴 리뷰(신규 코스 외 대비)
        self.cur.execute(f"DELETE FROM reviews WHERE member_id IN ({stu})")

        # 커뮤니티: 학생풀이 쓴 글/댓글
        self.cur.execute(f"SELECT post_id FROM posts WHERE author_id IN ({stu})")
        post_ids = [r["post_id"] for r in self.cur.fetchall()]
        if post_ids:
            self._del_in("comments", "post_id", post_ids)
            self._del_in("post_files", "post_id", post_ids)
            self._del_in("view_logs", "post_id", post_ids)
        self.cur.execute(f"DELETE FROM comments WHERE author_id IN ({stu})")
        self.cur.execute(f"DELETE FROM posts WHERE author_id IN ({stu})")

        # 스터디/채팅: 학생풀이 방장인 것 + 그 하위
        self.cur.execute(f"SELECT study_id FROM study WHERE host_id IN ({stu})")
        sids = [r["study_id"] for r in self.cur.fetchall()]
        self._del_in("study_participant", "study_id", sids)
        self._del_in("study_banned_member", "study_id", sids)
        self._del_in("study", "study_id", sids)
        self.cur.execute(f"SELECT chat_room_id FROM chat_room WHERE host_id IN ({stu})")
        rids = [r["chat_room_id"] for r in self.cur.fetchall()]
        self._del_in("chat_message", "chat_room_id", rids)
        self._del_in("chat_room_participant", "chat_room_id", rids)
        self._del_in("chat_room", "chat_room_id", rids)
        # 방어적: 학생풀이 참가자로 남은 행(우리 시드는 pool 안에서만 참가하므로 안전)
        self.cur.execute(f"DELETE FROM study_participant WHERE member_id IN ({stu})")
        self.cur.execute(f"DELETE FROM chat_room_participant WHERE member_id IN ({stu})")

        # 멤버
        self.cur.execute(
            f"DELETE FROM members WHERE member_id IN ({inst},{stu})")
        self.cur.execute("SET FOREIGN_KEY_CHECKS=1")

    def _member_student(self, mid, name, idx):
        self.x("""INSERT INTO members
            (member_id, name, email, username, password, role, status,
             is_locked, is_password_change_required, login_fail_count, optional_terms_agreed,
             profile_image_url, created_at)
            VALUES (%s,%s,%s,%s,%s,'STUDENT','ACTIVE', 0,0,0,1, %s,%s)""",
               (mid, name, f"stu_demo{idx}@flown.demo", f"stu_demo{idx}", PW_HASH,
                thumb_url(idx + 3), dt_str(TODAY_DT)))

    def students(self):
        """커뮤니티/리뷰/스터디/채팅 작성자용 학생풀. 페르소나와 무관."""
        self.pool = []
        for i, name in enumerate(STUDENT_NAMES):
            mid = STUDENT_IDS[i]
            self._member_student(mid, name, i)
            self.pool.append(mid)
        return len(self.pool)

    def instructors_and_courses(self):
        """대표 과목별 강사 + PUBLISHED 코스(섹션1·lesson N·정책). subject=enum명."""
        self.courses = []   # (course_id, subject_code)
        for idx, s in enumerate(SUBJECTS):
            mid = INSTRUCTOR_IDS[idx]
            uname = f"inst_demo{idx}"
            self.x("""INSERT INTO members
                (member_id, name, email, username, password, role, status,
                 is_locked, is_password_change_required, login_fail_count, optional_terms_agreed,
                 career, introduction, one_line_intro, profile_image_url, created_at)
                VALUES (%s,%s,%s,%s,%s,'INSTRUCTOR','ACTIVE', 0,0,0,1, %s,%s,%s,%s,%s)""",
                (mid, s["iname"], f"{uname}@flown.demo", uname, PW_HASH,
                 s["icareer"], s["iintro"], s["ione"], thumb_url(idx), dt_str(TODAY_DT)))

            price_type = "FREE" if s["price"] == 0 else "PAID"
            created = TODAY_DT - timedelta(days=idx * 3 + 5)
            course_id = self.x("""INSERT INTO course
                (author_id, price, title, created_at, description, price_type, status, subject,
                 thumbnail_url, learning_objectives, target_audience, tech_tags, level)
                VALUES (%s,%s,%s,%s,%s,%s,'PUBLISHED',%s,%s,%s,%s,%s,%s)""",
                (mid, s["price"], s["title"], dt_str(created),
                 f"{s['iname']} 강사의 {s['title']}. {s['iintro']}", price_type, s["code"],
                 thumb_url(idx),
                 "\n".join(s["obj"]), "\n".join(s["aud"]), "\n".join(s["tags"]), s["level"]))
            section_id = self.x(
                "INSERT INTO course_section (order_index, title, course_id) VALUES (0,%s,%s)",
                ("전체", course_id))
            for li in range(s["lessons"]):
                url, secs = DEMO_VIDEOS[li % len(DEMO_VIDEOS)]
                self.x("""INSERT INTO lesson
                    (created_at, order_index, title, section_id, duration_seconds, video_url, file_processing_status)
                    VALUES (%s,%s,%s,%s,%s,%s,'COMPLETED')""",
                    (dt_str(created), li, f"{li + 1}강", section_id, secs, url))
            diff = "HARD" if s["level"] == "심화" else ("EASY" if s["level"] == "입문" else "MEDIUM")
            self.x("""INSERT INTO course_learning_policy
                (course_id, recommended_duration_weeks, daily_recommended_minutes, difficulty, weekly_max_load_min)
                VALUES (%s, 8, 90, %s, 630)""", (course_id, diff))
            self.courses.append((course_id, s["code"]))
        return len(self.courses)

    def reviews(self):
        """신규 코스마다 학생풀 6~8명 리뷰 → reviewCount>0, 평점 노출."""
        total = 0
        for ci, (course_id, _) in enumerate(self.courses):
            cnt = 6 + (ci % 3)
            for k in range(cnt):
                mid = self.pool[(ci * 3 + k) % len(self.pool)]
                rating, content = REVIEW_TEMPLATES[(ci + k) % len(REVIEW_TEMPLATES)]
                created = TODAY_DT - timedelta(days=(k * 4 + ci) % 50 + 1)
                self.x("""INSERT INTO reviews
                    (member_id, course_id, rating, content, status, created_at, updated_at)
                    VALUES (%s,%s,%s,%s,'ACTIVE',%s,%s)""",
                    (mid, course_id, rating, content, dt_str(created), dt_str(created)))
                total += 1
        return total

    def _post(self, author_id, board_type, subject, title, content, accepted, created, views):
        return self.x("""INSERT INTO posts
            (author_id, board_type, subject, title, content, view_count, comment_count,
             is_accepted, status, created_at, updated_at)
            VALUES (%s,%s,%s,%s,%s,%s,0,%s,'ACTIVE',%s,%s)""",
            (author_id, board_type, subject, title, content, views,
             1 if accepted else 0, dt_str(created), dt_str(created)))

    def _comment(self, post_id, author_id, content, accepted, created):
        self.x("""INSERT INTO comments
            (accept_count, author_id, content, created_at, is_accepted, is_deleted,
             parent_id, post_id, status, updated_at)
            VALUES (%s,%s,%s,%s,%s,0, NULL,%s,'ACTIVE',%s)""",
            (1 if accepted else 0, author_id, content, dt_str(created),
             1 if accepted else 0, post_id, dt_str(created)))

    def community(self):
        """자유게시판 + 질문게시판(채택완료/대기 혼합) + 댓글. comment_count 동기화."""
        posts = 0
        comments = 0
        # 자유게시판
        for i, (title, content) in enumerate(FREE_POSTS):
            author = self.pool[i % len(self.pool)]
            created = TODAY_DT - timedelta(days=i * 2 + 1, hours=i)
            views = self.rnd.randint(15, 240)
            pid = self._post(author, "FREE", None, title, content, False, created, views)
            posts += 1
            ncmt = self.rnd.randint(0, 4)
            for c in range(ncmt):
                cauthor = self.pool[(i + c + 1) % len(self.pool)]
                self._comment(pid, cauthor, self.rnd.choice(ANSWER_TEMPLATES), False,
                              created + timedelta(hours=c + 1))
                comments += 1
            if ncmt:
                self.cur.execute("UPDATE posts SET comment_count=%s WHERE post_id=%s", (ncmt, pid))
        # 질문게시판
        for i, (code, title, content, accepted) in enumerate(QUESTION_POSTS):
            author = self.pool[(i + 5) % len(self.pool)]
            created = TODAY_DT - timedelta(days=i * 2 + 2, hours=i)
            views = self.rnd.randint(20, 320)
            pid = self._post(author, "QUESTION", code, title, content, accepted, created, views)
            posts += 1
            # 답변 댓글 1~3개, 채택글이면 첫 답변을 채택(is_accepted=1)
            nans = self.rnd.randint(1, 3)
            for c in range(nans):
                cauthor = self.pool[(i + c + 2) % len(self.pool)]
                is_acc = accepted and c == 0
                self._comment(pid, cauthor, self.rnd.choice(ANSWER_TEMPLATES), is_acc,
                              created + timedelta(hours=c + 2))
                comments += 1
            self.cur.execute("UPDATE posts SET comment_count=%s WHERE post_id=%s", (nans, pid))
        return posts, comments

    def studies_and_chat(self):
        """스터디 모집(모집중/마감) + 참가자 + 채팅방 + 과거 메시지."""
        studies = 0
        rooms = 0
        msgs = 0
        for i, (code, title, content, max_count, fill) in enumerate(STUDIES):
            host = self.pool[(i + 2) % len(self.pool)]
            fill = min(fill, max_count)
            status = "FULL" if fill >= max_count else STATUS_ACTIVE
            created = TODAY_DT - timedelta(days=i * 2 + 1, hours=i)
            study_id = self.x("""INSERT INTO study
                (host_id, title, subject, content, max_count, current_count, status, created_at, updated_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (host, title, code, content, max_count, fill, status,
                 dt_str(created), dt_str(created)))
            studies += 1
            # 참가자 = host + (fill-1)명 (pool 내 서로 다른 멤버)
            members = [host]
            j = 0
            while len(members) < fill:
                cand = self.pool[(i + 3 + j) % len(self.pool)]
                if cand not in members:
                    members.append(cand)
                j += 1
            for m in members:
                self.x("""INSERT INTO study_participant (study_id, member_id, joined_at)
                    VALUES (%s,%s,%s)""", (study_id, m, dt_str(created)))
            # 채팅방(study당 1개, study_id UNIQUE) + 참가자 + 메시지
            room_id = self.x("""INSERT INTO chat_room (study_id, host_id, status, created_at, updated_at)
                VALUES (%s,%s,'ACTIVE',%s,%s)""", (study_id, host, dt_str(created), dt_str(created)))
            rooms += 1
            for m in members:
                self.x("""INSERT INTO chat_room_participant (chat_room_id, member_id, joined_at)
                    VALUES (%s,%s,%s)""", (room_id, m, dt_str(created)))
            nmsg = self.rnd.randint(4, len(CHAT_LINES))
            base = created + timedelta(hours=1)
            for k in range(nmsg):
                sender = members[k % len(members)]
                line = CHAT_LINES[k % len(CHAT_LINES)]
                sent = base + timedelta(minutes=k * 7)
                if self._chat_has_type:
                    self.x("""INSERT INTO chat_message (chat_room_id, sender_id, content, sent_at, type)
                        VALUES (%s,%s,%s,%s,'CHAT')""", (room_id, sender, line, dt_str(sent)))
                else:
                    self.x("""INSERT INTO chat_message (chat_room_id, sender_id, content, sent_at)
                        VALUES (%s,%s,%s,%s)""", (room_id, sender, line, dt_str(sent)))
                msgs += 1
        return studies, rooms, msgs


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    conn = pymysql.connect(
        host=os.environ.get("DB_HOST", "127.0.0.1"),
        port=int(os.environ.get("DB_PORT", 3306)),
        user=os.environ["DB_USER"], password=os.environ["DB_PASSWORD"],
        database=os.environ.get("DB_NAME", "hardclick_db"),
        cursorclass=DictCursor, autocommit=False,
    )
    s = ContentSeeder(conn)
    if not (_S3_BUCKET and THUMB_KEYS):
        print("⚠ S3 썸네일 매핑 없음 → picsum 폴백 URL 사용 (로컬 이미지 렌더하려면 upload_demo_thumbs 먼저).")
    print("wiping content demo data (신규 대역 9240~9259 / 9300~9349만)…")
    s.wipe_content()
    print(f"seeding students… ({s.students()}명)")
    print(f"seeding instructors+courses… ({s.instructors_and_courses()}코스)")
    print(f"seeding reviews… ({s.reviews()}건)")
    posts, comments = s.community()
    print(f"seeding community… (게시글 {posts} / 댓글 {comments})")
    studies, rooms, msgs = s.studies_and_chat()
    print(f"seeding study+chat… (스터디 {studies} / 채팅방 {rooms} / 메시지 {msgs})")
    conn.commit()
    print("\n=== 완료. 과목 필터 확인용 subject 코드 ===")
    print(", ".join(sub["code"] for sub in SUBJECTS))
    conn.close()


if __name__ == "__main__":
    main()
