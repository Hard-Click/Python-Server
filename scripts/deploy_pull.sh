#!/usr/bin/env bash
# Python-Server 배포 스크립트 (EC2 app 인스턴스에서 실행).
# GitHub Actions가 SSM RunCommand로 이걸 돌리거나, 부팅 시 user-data가 호출한다.
# 멱등(idempotent): 몇 번을 돌려도 결과가 같다. 시드 데이터는 절대 건드리지 않는다.
set -euo pipefail

REPO_DIR="${REPO_DIR:-/home/ssm-user/Python-Server}"
BRANCH="${DEPLOY_BRANCH:-main}"
VENV="${VENV:-$REPO_DIR/.venv}"
LOG_DIR="${LOG_DIR:-/home/ssm-user/logs}"

echo "[deploy] $(date -Is) repo=$REPO_DIR branch=$BRANCH"
mkdir -p "$LOG_DIR"
cd "$REPO_DIR"

# 1) 최신 코드로 강제 정합 (수동 pull로 생긴 로컬 dirty가 있어도 안전하게 origin 기준으로 맞춤).
#    review_card 등 DB가 아니라 '코드 클론'만 리셋한다 — DB/시드는 무관.
git fetch origin --prune
git checkout -q "$BRANCH"
git reset --hard "origin/$BRANCH"

# 2) 의존성 정합 (venv 없으면 생성).
if [ ! -x "$VENV/bin/python" ]; then
  python3 -m venv "$VENV"
fi
"$VENV/bin/pip" install -q -r requirements.txt

# 3) 배치 파일 존재 확인 — 이번 장애의 직접 증상이었던 부분.
test -f presentation/jobs/review_update.py || { echo "[deploy] FATAL: review_update.py 없음"; exit 1; }

# 4) crontab 정합 — 레포에 crontab이 없으므로 배포가 서버 cron을 보장한다(멱등 재작성).
install_cron() {
  local marker="# >>> python-server managed cron >>>"
  local endmk="# <<< python-server managed cron <<<"
  local wrapper="$REPO_DIR/scripts/run_review_update.sh"
  local tmp; tmp="$(mktemp)"
  # 기존 관리블록 제거 후 재삽입.
  crontab -l 2>/dev/null | sed "/$marker/,/$endmk/d" > "$tmp" || true
  {
    echo "$marker"
    echo "# 매일 02:00 - 복습 카드(FSRS) 갱신. review_card.due 생산자."
    echo "0 2 * * * $wrapper >> $LOG_DIR/review_update.log 2>&1"
    echo "$endmk"
  } >> "$tmp"
  crontab "$tmp"
  rm -f "$tmp"
}
install_cron

echo "[deploy] done. HEAD=$(git rev-parse --short HEAD)"
