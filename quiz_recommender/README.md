# Quiz Recommender

퀴즈 복습 시 **틀린 문제 + 유사 문제**를 묶어주는 추천 모듈.
Gemini 임베딩 + Qdrant 벡터 검색.

> 종준 스케줄러(`Python-Server`)와 **같은 레포(모노레포)**의 독립 폴더다.
> 문제 데이터는 같은 RDS를 읽는다("RDS 공유" 철학 동일).
> **통합은 실시간 HTTP**: 백엔드(Spring) `SimilarProblemRecommenderAdapter`가 학생의 유사퀴즈
> 요청 시 `GET /quiz/similar/{problemId}?student_id=&k=`로 이 서버(app.py)를 호출한다.
> (초기 설계였던 '종준 FSRS 배치의 함수 직접 호출'은 폐기 — 2026-07-20 확정.
> 야간 배치는 추천을 소비하지 않으므로 크론 시각과 인덱스 신선도는 무관하다.)

## 구성
```
recommender.py   진입 함수 get_similar_problems() ← app.py(HTTP)가 호출
indexer.py       배치: RDS에서 문제 읽어 임베딩 → Qdrant 동기화
vector_store.py  Qdrant 저장/검색 (questionId = point id)
embedding.py     Gemini 임베딩 (배치 호출, gemini-embedding-001)
db.py            공유 RDS(MySQL) 연결
config.py        환경변수 설정
app.py           프로덕션 서빙 API — 백엔드가 HTTP로 호출 (monitoring EC2 systemd 상시 구동)
```

## 동작 방식
```
[배치] indexer.py ──RDS에서 문제 읽기──▶ Gemini 임베딩 ──▶ Qdrant
[추천] FE → 백엔드(SimilarQuizService) ──GET /quiz/similar/{id}──▶ app.py ──▶ Qdrant 검색 ──▶ [원문제, 유사...]
```
- **인덱싱은 배치**가 RDS를 직접 읽어 처리 (크론: monitoring EC2, KST 02:30).
- **추천은 실시간 HTTP** — 학생이 유사퀴즈 화면에 진입할 때 백엔드가 오답별로 호출.

## 진입 함수
```python
from quiz_recommender import get_similar_problems   # Python-Server/ 루트 기준

get_similar_problems(student_id, problem_id, k=2)
# 정상+유사 충분 → [원문제, 유사1, 유사2]
# 유사 1개뿐     → [원문제, 유사1]
# 유사 없음       → [원문제]        (원문제 있음 = 정상)
# 장애(RDS/Qdrant)→ [원문제]        (예외 안 던짐 — 추천만 조용히 스킵)
# 잘못된 id       → []              (빈 리스트 = 에러)
# 규칙: 결과가 비었으면 잘못된 id, 원문제가 있으면 정상(유사 0~k개 가변)
```
폴백(유사 k개 안 채워지면 단계적 완화):
1. 같은 course + section + 같은 난이도
2. 같은 course + section + 난이도 ±1
3. 같은 course + section (난이도 무시)
4. 같은 course (인접 섹션 포함)
→ 그래도 없으면 원문제만 반환.

> **백엔드 통합 확정(계약)** — Spring `SimilarProblemRecommenderAdapter` ↔ app.py:
> - 호출: `GET {QUIZ_AI_BASE_URL}/quiz/similar/{problemId}?student_id={memberId}&k=2`
>   (SimilarQuizService가 학생의 오답마다 1회 호출, `quiz.ai.enabled=false`면 호출 자체를 스킵)
> - 에러 방식(정책 ⓐ): RDS/Qdrant **장애 시에도 예외를 던지지 않고 `[원문제]` 반환**,
>   서버 자체가 죽으면 백엔드 어댑터가 빈 리스트 폴백 → 어느 쪽이 죽어도 화면 에러 없음.
> - 백엔드 env: `QUIZ_AI_ENABLED` / `QUIZ_AI_BASE_URL` (SSM `/hard-click/prod/QUIZ_AI_*` → compose 전달).

## 세팅
```bash
python -m venv .venv && .venv\Scripts\activate    # Windows
pip install -r requirements.txt
copy .env.example .env    # 값 채우기 (Gemini/Qdrant/RDS)
```
> ⚠️ Gemini 키는 Google AI Studio(aistudio.google.com/apikey)에서 발급. RDS는 **읽기 전용 계정** 권장.

## 실행
```bash
# 1) 배치 인덱싱 (크론 등록 권장 — 예: 10분마다 또는 야간)
python indexer.py
#   → {"total": 5000, "embedded": 12, "deleted": 1}  (바뀐 것만 임베딩)

# 2) 서빙 API (프로덕션: monitoring EC2 systemd quiz-recommender.service로 상시 구동)
uvicorn app:app --host 0.0.0.0 --port 8000
#   GET /quiz/similar/101?k=2  → {"problems": [101, 140, 178]}
```

## 설계 메모
- `questionId`(BIGINT)를 Qdrant point id로 사용 → 검색 시 저장된 벡터 재사용(텍스트 재전송 불필요).
- 인덱서는 `content_hash`(본문+해설+난이도) 비교로 **바뀐 문제만** 재임베딩 → 비용 최소화, 수정 자동 반영.
- RDS에서 삭제된 문제는 인덱서가 Qdrant에서도 제거(동기화).
- 추천은 같은 `course` 내, 원문제 자신은 유사 후보에서 제외(단 결과 맨 앞에 원문제 포함).

## ⚠️ 선행 작업 (백엔드)
- `quiz_question`에 **`difficulty` 컬럼 추가** 필요 → 백엔드 마이그레이션(`db/migration/V___.sql`) + CI 드리프트 게이트.
  난이도 폴백(±1)을 쓰려면 **정수 레벨**(예: 1=하, 2=중, 3=상) 권장. 컬럼 없으면 코드가 자동으로 section/course 폴백만 사용.
- 참조 테이블: `quiz`(quiz_id, course_id, section_id), `quiz_question`(question_id, quiz_id, question_text, explanation, **+difficulty**).
