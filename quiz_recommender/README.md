# Quiz Recommender

퀴즈 복습 시 **틀린 문제 + 유사 문제**를 묶어주는 추천 모듈.
Gemini 임베딩 + Qdrant 벡터 검색.

> 종준 스케줄러(`Python-Server`)와 **같은 레포(모노레포)**의 독립 폴더다.
> 문제 데이터는 같은 RDS를 읽는다("RDS 공유" 철학 동일).
> **통합은 함수 호출**: 종준 FSRS가 `recommender.get_similar_problems()`를 같은 프로세스에서 직접 호출한다(HTTP 아님).

## 구성
```
recommender.py   진입 함수 get_similar_problems() ← 종준 FSRS가 호출
indexer.py       배치: RDS에서 문제 읽어 임베딩 → Qdrant 동기화
vector_store.py  Qdrant 저장/검색 (questionId = point id)
embedding.py     Gemini 임베딩 (배치 호출, gemini-embedding-001)
db.py            공유 RDS(MySQL) 연결
config.py        환경변수 설정
app.py           로컬 테스트용 HTTP 래퍼 (선택 — 프로덕션 경로 아님)
```

## 동작 방식
```
[배치] indexer.py ──RDS에서 문제 읽기──▶ Gemini 임베딩 ──▶ Qdrant
[추천] 종준 FSRS ──get_similar_problems(problem_id, k)──▶ Qdrant 검색 ──▶ [원문제, 유사...]
```
- **인덱싱은 배치**가 RDS를 직접 읽어 처리.
- **추천은 함수 호출** — FSRS가 필요할 때 import 해서 호출.

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

> **종준 FSRS 통합 확정(계약)**:
> - import 경로: `from quiz_recommender import get_similar_problems` (Python-Server/ 루트 실행)
> - 호출: 새벽 리프레시 배치가 학생별로 틀린 `question_id`마다 sync 1회 루프 호출
> - 에러 방식(정책 ⓐ): RDS/Qdrant **장애 시에도 예외를 던지지 않고 `[원문제]` 반환** → 배치는 `[]`=잘못된 id skip, `[원문제]`=추천 없음 skip 으로 처리하면 예외 처리 불필요.

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

# 2) (선택) 로컬 테스트 HTTP 래퍼
uvicorn app:app --port 8000
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
