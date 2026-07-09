# Quiz Recommender

퀴즈 복습 시 **틀린 문제와 유사한 문제**를 찾아주는 별도 AI 서비스.
OpenAI 임베딩 + Qdrant 벡터 검색.

> 종준이의 스케줄러 서버(`Python-Server`)와는 **별개 서비스**다. 다만 문제 데이터는
> 같은 RDS를 읽는다(스케줄러 서버의 "RDS 공유" 방식과 동일 철학).

## 구성
```
app.py           추천 엔드포인트 (FastAPI, 경량)
indexer.py       배치: RDS에서 문제 읽어 임베딩 → Qdrant 동기화
db.py            공유 RDS(MySQL) 연결
embedding.py     OpenAI 임베딩 (배치 호출)
vector_store.py  Qdrant 저장/검색 (questionId = point id)
config.py        환경변수 설정
```

## 동작 방식
```
[배치] indexer.py ──RDS에서 문제 읽기──▶ OpenAI 임베딩 ──▶ Qdrant
[추천] Spring ──POST /quiz/reviews/recommend──▶ app.py ──Qdrant 검색──▶ 유사 questionId
```
- **인덱싱은 배치**가 RDS를 직접 읽어 처리 → Spring에 index/delete 호출을 만들 필요 없음.
- **추천만 엔드포인트** → Spring은 복습 조회 시 한 번 호출.

## 세팅
```bash
python -m venv .venv && .venv\Scripts\activate    # Windows
pip install -r requirements.txt
copy .env.example .env    # 값 채우기 (OpenAI/Qdrant/RDS)
```
> ⚠️ OpenAI 대시보드에서 **월 사용 한도(예: $5)** 꼭 설정. RDS는 **읽기 전용 계정** 권장.

## 실행
```bash
# 1) 배치 인덱싱 (크론 등록 권장 — 예: 10분마다 또는 야간)
python indexer.py
#   → {"total": 5000, "embedded": 12, "deleted": 1}  (바뀐 것만 임베딩)

# 2) 추천 API
uvicorn app:app --host 0.0.0.0 --port 8000
```

## 엔드포인트
`POST /quiz/reviews/recommend`
```json
{
  "wrongQuestionIds": [101, 205],
  "courseId": 5,
  "excludeQuestionIds": [101, 205, 300],
  "perQuestion": 2
}
```
→
```json
{
  "recommendations": [
    {"wrongQuestionId": 101, "similarQuestionIds": [140, 178]},
    {"wrongQuestionId": 205, "similarQuestionIds": [210, 233]}
  ]
}
```

## 설계 메모
- `questionId`(BIGINT)를 Qdrant point id로 사용 → 추천 시 저장된 벡터 재사용(텍스트 재전송 불필요).
- 인덱서는 `content_hash` 비교로 **새/수정된 문제만** 재임베딩 → 비용 최소화, 문제 수정도 자동 반영.
- RDS에서 삭제된 문제는 인덱서가 Qdrant에서도 제거(동기화).
- 추천은 같은 `courseId` 내에서만, 본인·이미 푼 문제 제외.
- 참조 테이블: `quiz`(quiz_id, course_id, section_id), `quiz_question`(question_id, quiz_id, question_text, explanation).
