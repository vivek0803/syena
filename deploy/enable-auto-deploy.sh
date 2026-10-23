#!/usr/bin/env bash
# One-time: makes the server deploy new releases from GitHub on its own.  sudo bash deploy/enable-auto-deploy.sh
# 1) Gives the server its own read-only key for your GitHub repo (no token typing on every update).
# 2) Installs a timer that runs deploy/auto-deploy.sh every 3 minutes.
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "Run as root: sudo bash $0"; exit 1; }
cd "$(dirname "$0")/.."
DIR=$(pwd)
REPO=$(git remote get-url origin | sed -E 's#(https://github.com/|git@github.com:)##; s#\.git$##')
KEY=/root/.ssh/syena_deploy

mkdir -p /root/.ssh && chmod 700 /root/.ssh
[ -f "$KEY" ] || ssh-keygen -q -t ed25519 -N "" -C "syena-server-deploy" -f "$KEY"
grep -q "syena_deploy" /root/.ssh/config 2>/dev/null || printf 'Host github.com\n  IdentityFile %s\n  IdentitiesOnly yes\n' "$KEY" >> /root/.ssh/config
ssh-keyscan -t ed25519 github.com 2>/dev/null >> /root/.ssh/known_hosts
sort -u -o /root/.ssh/known_hosts /root/.ssh/known_hosts

if ! git ls-remote "git@github.com:$REPO.git" >/dev/null 2>&1; then
  echo
  echo "Add this key to GitHub: github.com/$REPO -> Settings -> Deploy keys -> Add deploy key"
  echo "  Title: syena server     Key (copy the whole line below)     leave 'Allow write access' OFF"
  echo
  cat "$KEY.pub"
  echo
  read -r -p "Press Enter after you've added it... " _ </dev/tty
  git ls-remote "git@github.com:$REPO.git" >/dev/null || { echo "GitHub still refuses the key. Check it was saved, then run this again."; exit 1; }
fi
git remote set-url origin "git@github.com:$REPO.git"
git config --global --add safe.directory "$DIR" 2>/dev/null || true

cat > /etc/systemd/system/syena-autodeploy.service <<UNIT
[Unit]
Description=Deploy new Syena releases from GitHub
After=network-online.target docker.service
[Service]
Type=oneshot
ExecStart=/bin/bash $DIR/deploy/auto-deploy.sh
TimeoutStartSec=45min
UNIT
cat > /etc/systemd/system/syena-autodeploy.timer <<UNIT
[Unit]
Description=Check GitHub for a new Syena release every 3 minutes
[Timer]
OnBootSec=2min
OnUnitActiveSec=3min
[Install]
WantedBy=timers.target
UNIT
systemctl daemon-reload
systemctl enable --now syena-autodeploy.timer
echo
echo "Auto-deploy is on. Push from the laptop (windows\\publish-code.bat); once GitHub's tests pass, the server"
echo "deploys within a few minutes (after 15:35 on market days). Watch it:  sudo tail -f /var/log/syena-deploy.log"
