# Python-Server (돼지엄마 AI 스케줄러)

## 구조 (Clean Architecture — hc-backend와 대칭)
```
domain/          순수 알고리즘 (CP-SAT, FSRS, 이탈위험). DB·프레임워크 import 절대 금지.
application/     유스케이스 + ports.py(Protocol 인터페이스). domain을 조합만 함.
infrastructure/  실제 RDS 쿼리, error-router 연동. ports.py를 구현.
presentation/    Flask 엔드포인트(api.py) + 크론 진입점(jobs/).
```

## 절대 규칙
- `domain/`은 `infrastructure/`를 import하면 안 됨(역방향 금지). 위반되면 구조가 무너짐.
- DB 스키마가 바뀌면 **`infrastructure/repositories.py`만** 고친다. `domain/`·`application/`은 손대지 않는다 — 이게 이 구조로 나눈 이유.
- 비밀번호·API키는 절대 코드에 하드코딩 금지, 전부 환경변수(`DB_HOST` 등)로만.

## 알아둘 것 (코드만 봐선 모르는 것)
- `infrastructure/repositories.py`의 SQL은 **PO 설계 문서 기준 추정 스키마**임. 실제 마이그레이션 확정되면 컬럼명 검증 필요.
- FSRS 리뷰 테스트 시 **리뷰 사이 실제 날짜 간격을 둘 것** — 같은 카드로 즉시 연달아 review_card 호출하면 same-day 로직 때문에 stability가 비정상적으로 나옴(당일 재리뷰로 처리됨).
- 퀴즈점수→FSRS grade 임계값(90/70/50%)은 우리가 직접 정한 값 — 실전에 정립된 공식 없음, 실측 쌓이면 조정 가능.
- 배치 실패는 `infrastructure/error_router_client.py`로 기존 error-router(`/webhook/error`)에 알림 — 새 알림 채널 안 만듦.
- `presentation/jobs/weekly_reflow.py`의 `total_weekly_minutes=420`은 TODO 플레이스홀더 — 학생별 daily_cap 조회로 교체 필요.

## 실행
```bash
pip install -r requirements.txt
pytest tests/                        # domain 로직 테스트 (DB 불필요)
python -m presentation.jobs.weekly_reflow   # 실제 배치 (DB 연결 환경변수 필요)
python presentation/api.py           # 온보딩 즉시생성 엔드포인트
```
