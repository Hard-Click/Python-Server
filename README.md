# Python-Server

Flown 돼지엄마 AI 스케줄러 — CP-SAT 시간표 배정 + FSRS 복습주기 + 이탈위험 예측.
Spring 백엔드와는 직접 통신하지 않고 RDS를 공유(배치 write / 온보딩만 경량 엔드포인트 예외).

## 구조 (Clean Architecture, hc-backend와 대칭 — 상세는 [CLAUDE.md](./CLAUDE.md))
- `domain/` — 순수 알고리즘: CP-SAT 배정, FSRS 복습, 이탈위험 규칙기반
- `application/` — 유스케이스 + repository 인터페이스(ports.py)
- `infrastructure/` — 실제 RDS 쿼리, error-router 알림 연동
- `presentation/` — 온보딩 즉시생성 엔드포인트(api.py) + 크론 진입점(jobs/)

## 설치
```bash
python -m venv .venv
.venv/Scripts/activate  # Windows
pip install -r requirements.txt
```

## 테스트
```bash
python test_scheduler.py
python review/fsrs_review.py
python dropout/rule_based_risk.py
```

## 설계 배경
전체 모델 선택 근거·정책(Frozen Zone, 다중코스 cap 분배 등)은 PO 노트 참고.
핵심만 요약:
- 배정: OR-Tools CP-SAT (학습데이터 불필요, 콜드스타트 자동 해결)
- 복습: FSRS — 전역 기본가중치로 1일차부터 작동, 개인 리뷰 쌓이면 재학습
- 이탈위험: 규칙기반(recency+스트릭) → 이탈 이벤트 데이터 쌓이면 Cox PH로 승급
