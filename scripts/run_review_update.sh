#!/usr/bin/env bash
# 야간 복습 배치 래퍼 + heartbeat.
# 왜 래퍼가 필요한가: review_update.py 내부의 error-router 알림은 '파이썬이 모듈을 import한 뒤'에만
# 동작한다. 이번 장애처럼 파일이 없어 프로세스가 시작조차 못 하면 앱 알림은 원천적으로 못 간다.
# 그래서 성공했을 때만 외부 모니터(dead-man's-switch)에 ping을 쏘고, 'ping이 안 오면' 외부에서
# 실패로 감지하게 한다. HEARTBEAT_URL 은 healthchecks.io 등의 체크 URL.
set -uo pipefail

REPO_DIR="${REPO_DIR:-/home/ssm-user/Python-Server}"
VENV="${VENV:-$REPO_DIR/.venv}"
ENV_CRON="${ENV_CRON:-$REPO_DIR/.env.cron}"
HEARTBEAT_URL="${HEARTBEAT_URL:-}"

cd "$REPO_DIR" || exit 1

# 배치 환경변수 로드 (DB 접속 등).
if [ -f "$ENV_CRON" ]; then
  set -a; . "$ENV_CRON"; set +a
fi

# 실패 시작 신호(선택): /fail 엔드포인트가 있으면 즉시 빨간불.
[ -n "$HEARTBEAT_URL" ] && curl -fsS -m 10 "$HEARTBEAT_URL/start" >/dev/null 2>&1 || true

"$VENV/bin/python" -m presentation.jobs.review_update
rc=$?

if [ "$rc" -eq 0 ]; then
  # 성공했을 때만 ping. 안 오면 외부 모니터가 알아서 알림.
  [ -n "$HEARTBEAT_URL" ] && curl -fsS -m 10 "$HEARTBEAT_URL" >/dev/null 2>&1 || true
else
  [ -n "$HEARTBEAT_URL" ] && curl -fsS -m 10 "$HEARTBEAT_URL/fail" >/dev/null 2>&1 || true
fi
exit "$rc"
