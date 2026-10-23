#!/usr/bin/env bash
# Runs every few minutes on the server (systemd timer, set up by deploy/enable-auto-deploy.sh).
# If GitHub has a new "release" (a push to main that passed the tests), it deploys it with deploy/deploy.sh.
# During market hours (Mon-Fri 09:00-15:35 IST) it waits until after the close, so a restart never interrupts
# live trading. Deploy straight away anyway:  sudo bash /opt/syena/deploy/auto-deploy.sh --now
set -uo pipefail
cd "$(dirname "$0")/.."
BRANCH="${SYENA_DEPLOY_BRANCH:-release}"
STATE=/var/lib/syena-deploy; mkdir -p "$STATE"
LOG=/var/log/syena-deploy.log
NOW_FLAG="${1:-}"

exec 9>"$STATE/lock"
flock -n 9 || exit 0                                 # a deploy is already running

git fetch -q origin "$BRANCH" 2>>"$LOG" || { echo "$(date '+%F %T') fetch failed" >> "$LOG"; exit 1; }
NEW=$(git rev-parse "origin/$BRANCH")
[ "$NEW" = "$(git rev-parse HEAD)" ] && exit 0      # already running the latest release
[ "$NEW" = "$(cat "$STATE/failed" 2>/dev/null)" ] && [ "$NOW_FLAG" != --now ] && exit 0   # this one failed before

dow=$(TZ=Asia/Kolkata date +%u); hm=$(TZ=Asia/Kolkata date +%H%M)
anytime=$(grep -s '^SYENA_DEPLOY_ANYTIME=' deploy/.env | cut -d= -f2)
if [ "$NOW_FLAG" != --now ] && [ "$anytime" != 1 ] && [ "$dow" -le 5 ] && [ "$hm" -ge 0900 ] && [ "$hm" -lt 1535 ]; then
  if [ "$(cat "$STATE/waiting" 2>/dev/null)" != "$NEW" ]; then
    echo "$NEW" > "$STATE/waiting"; echo "$(date '+%F %T') new release ${NEW:0:7} waits for market close (15:35)" >> "$LOG"
  fi
  exit 0
fi

notify() {   # Telegram message, if Syena's bot is configured
  local tok chat
  tok=$(grep -s '^SYENA_TELEGRAM_BOT_TOKEN=' deploy/.env | cut -d= -f2-); chat=$(grep -s '^SYENA_TELEGRAM_CHAT_ID=' deploy/.env | cut -d= -f2-)
  [ -n "$tok" ] && [ -n "$chat" ] && curl -fsS -m 10 "https://api.telegram.org/bot$tok/sendMessage" \
    --data-urlencode "chat_id=$chat" --data-urlencode "text=$1" >/dev/null 2>&1 || true
}

OLD=$(git rev-parse HEAD)
MSG=$(git log -1 --format=%s "$NEW")
echo "$(date '+%F %T') deploying ${NEW:0:7}: $MSG" >> "$LOG"
git checkout -q -B "$BRANCH" "$NEW" && git branch -q --set-upstream-to="origin/$BRANCH" "$BRANCH"
if SYENA_SKIP_PULL=1 bash deploy/deploy.sh >> "$LOG" 2>&1; then
  rm -f "$STATE/failed" "$STATE/waiting"
  echo "$(date '+%F %T') deployed ${NEW:0:7}" >> "$LOG"
  notify "Syena updated on the server (${NEW:0:7}): $MSG. Mode is PAPER."
else
  echo "$NEW" > "$STATE/failed"
  echo "$(date '+%F %T') FAILED ${NEW:0:7}; going back to ${OLD:0:7}" >> "$LOG"
  git checkout -q -B "$BRANCH" "$OLD"
  SYENA_SKIP_PULL=1 bash deploy/deploy.sh >> "$LOG" 2>&1 || true
  notify "Syena update ${NEW:0:7} FAILED on the server and was rolled back. Details: sudo tail -80 $LOG"
fi
