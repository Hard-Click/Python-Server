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
- `infrastructure/repositories.py`의 공유 레포 SQL은 **종호(DBA) 실 마이그레이션(V3.1.x/V3.3.x) 기준으로 정합됨**(배포 전이라 대상 테이블 없음 에러 가능 — 정상). 단 스케줄러용 추가 레포 일부(`MySQLLessonProgressRepository`/`StudentNotification`/`Experiment`/`Subscription`)는 아직 **추정 스키마** — 배치 실행 전 검증 필요.
- FSRS 리뷰 테스트 시 **리뷰 사이 실제 날짜 간격을 둘 것** — 같은 카드로 즉시 연달아 review_card 호출하면 same-day 로직 때문에 stability가 비정상적으로 나옴(당일 재리뷰로 처리됨).
- 퀴즈점수→FSRS grade 임계값(90/70/50%)은 우리가 직접 정한 값 — 실전에 정립된 공식 없음, 실측 쌓이면 조정 가능.
- 배치 실패는 `infrastructure/error_router_client.py`로 기존 error-router(`/webhook/error`)에 알림 — 새 알림 채널 안 만듦.
- `total_weekly_minutes`는 `MySQLStudentCapRepository.get_weekly_available_minutes(member_id)`로 조회함(온보딩 daily_cap_min×(7-쉬는날)). 온보딩 미완료 학생은 `DEFAULT_WEEKLY_AVAILABLE_MINUTES=420`으로 폴백 — 이 상수만 콜드스타트 placeholder고 정상 학생은 실제 조회값을 씀.
- **스케줄 소유 모델**: 학생은 AI 계획을 직접 편집하지 않는다 — 일별 실측만 `daily_achievement`에 기록하면 야간 `NightlyReflowUseCase`(남은 주 재분배) + 주간 `GenerateWeeklyScheduleUseCase`(효율계수 재계산·재생성)가 자동 반영. `weekly_schedule`는 `effective_from`(≤오늘 중 최신본이 활성) + `locked`(1=Frozen Zone, 리플로우 제외) 모델(종호 실 스키마): 새 계획은 `locked=0`으로 넣고, 야간 배치가 '이번 주'에 `locked=1`을 세팅해 다음 리플로우부터 보호한다. `schedule_slot`은 주차offset이 아니라 `plan_date`·`lesson_id`. reflow 조회·수정은 `locked=0`만 대상.
- `GenerateWeeklyScheduleUseCase.execute(..., commit=)`: `commit=False`(미리보기)는 CP-SAT 계산만, 저장·알림·실험로그(exposure/shadow) 전부 생략. `/generate-preview`=commit False, `/generate-commit`·주간 배치=commit True. preview가 DB·shadow 로그 오염 못 하게 분리.
- 코드 곳곳의 매직넘버(SLIP_BUFFER_WEEKS, EFFICIENCY_STRETCH_FACTOR, risk 가중치 등)는 근거·재검토 조건이 [docs/policy_constants.md](docs/policy_constants.md)에 정리돼 있음 — 값을 고치기 전에 먼저 그 문서를 볼 것. risk 가중치는 `scripts/calibrate_risk_weights.py`로 실측 데이터 재검증 가능(실측 없으면 synthetic 폴백, 그 결과로 교체하면 안 됨).

## 실행
```bash
pip install -r requirements.txt
pytest tests/                        # domain 로직 테스트 (DB 불필요)
python -m presentation.jobs.weekly_reflow    # 주간 리플로우 배치 (DB 연결 환경변수 필요)
python -m presentation.jobs.review_update    # 복습 카드(FSRS) 갱신 배치 (야간) - 이걸 안 돌리면
                                              # review_card.due 가 안 채워져서 스케줄에 복습이 안 뜬다
python presentation/api.py           # 온보딩 즉시생성 엔드포인트
```

## 배포(EC2) — 자동화됨 (2026-07-22)
**과거의 함정(장애 원인):** 예전엔 CD가 없어 EC2에서 손으로 `git pull`, crontab도 서버에 수동 등록이었다.
그 결과 배포 클론이 stale해져 `review_update.py`가 없는 채로 야간 배치가 매일 죽고 복습이 캘린더에 안 떴다.

이제 **`main` 머지 → GitHub Actions(`.github/workflows/deploy-python-server.yml`)** 가 SSM RunCommand로
app ASG 인스턴스에서 [`scripts/deploy_pull.sh`](scripts/deploy_pull.sh)를 돌린다. 이 스크립트가:
- `git reset --hard origin/main` 으로 코드 정합 (DB/시드 무관, 코드 클론만),
- venv/의존성 정합,
- **crontab을 멱등 재작성**(관리 블록) — 배치 등록이 더 이상 수동이 아님.

크론은 파이썬을 직접 부르지 않고 [`scripts/run_review_update.sh`](scripts/run_review_update.sh) 래퍼를 부른다
(성공 시 `HEARTBEAT_URL`로 ping → '안 오면' 외부 모니터가 실패 감지. 파일 없음처럼 프로세스가 시작조차
못 하는 실패는 앱 알림으로 못 잡기 때문).

**ASG 재생성 대비:** launch template user-data에 `deploy_pull.sh`를 걸어두면 새 인스턴스도 부팅 시 자기정합됨.
**필요 세팅(1회):** GitHub repo secret `AWS_DEPLOY_ROLE_ARN`(OIDC assume 역할), 인스턴스에 SSM 권한,
`.env.cron`에 `HEARTBEAT_URL`.
