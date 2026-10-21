#!/usr/bin/env bash
# One-time setup of a fresh Ubuntu 22.04/24.04 server for Syena. Run as root (or with sudo):
#   curl -fsSL https://raw.githubusercontent.com/<you>/syena/main/deploy/server-setup.sh | sudo bash
#   (or copy this file to the server and run:  sudo bash server-setup.sh)
# It installs Docker, a firewall (only SSH, HTTP, HTTPS open), automatic security updates, swap, Indian time, then
# clones the repo to /opt/syena and creates deploy/.env with fresh random secrets. API keys are added by you after.
set -euo pipefail
REPO_URL="${REPO_URL:-https://github.com/vivek0803/syena.git}"
DIR=/opt/syena

[ "$(id -u)" = 0 ] || { echo "Run as root: sudo bash $0"; exit 1; }
export DEBIAN_FRONTEND=noninteractive

echo "==> system updates, firewall, automatic security updates"
apt-get update -q
apt-get install -yq ca-certificates curl git unattended-upgrades fail2ban openssl
[ -f /etc/iptables/rules.v4 ] || apt-get install -yq ufw
dpkg-reconfigure -f noninteractive unattended-upgrades
timedatectl set-timezone Asia/Kolkata
if [ -f /etc/iptables/rules.v4 ] && grep -q "REJECT" /etc/iptables/rules.v4; then
  # Oracle Cloud images ship their own iptables firewall that blocks everything except SSH. Open web ports there
  # (ufw would fight with it). Also open 80/443 in the VCN security list in the Oracle console.
  echo "==> Oracle Cloud image detected: opening ports 80 and 443 in its iptables firewall"
  for p in 80 443; do
    iptables -C INPUT -p tcp --dport $p -m state --state NEW -j ACCEPT 2>/dev/null || \
      iptables -I INPUT 6 -p tcp --dport $p -m state --state NEW -j ACCEPT
  done
  iptables -C INPUT -p udp --dport 443 -j ACCEPT 2>/dev/null || iptables -I INPUT 6 -p udp --dport 443 -j ACCEPT
  netfilter-persistent save >/dev/null 2>&1 || iptables-save > /etc/iptables/rules.v4
else
  ufw default deny incoming >/dev/null
  ufw default allow outgoing >/dev/null
  ufw allow OpenSSH >/dev/null
  ufw allow 80/tcp >/dev/null
  ufw allow 443/tcp >/dev/null
  ufw allow 443/udp >/dev/null
  ufw --force enable
fi
systemctl enable --now fail2ban

if [ "$(free -m | awk '/^Mem:/{print $2}')" -lt 3500 ] && ! swapon --show | grep -q /swapfile; then
  echo "==> adding 2 GB swap (small server)"
  fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile >/dev/null && swapon /swapfile
  echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

if ! command -v docker >/dev/null; then
  echo "==> installing Docker"
  curl -fsSL https://get.docker.com | sh
fi
systemctl enable --now docker

echo "==> getting the code"
if [ -d "$DIR/.git" ]; then
  git -C "$DIR" pull --ff-only
else
  git clone "$REPO_URL" "$DIR"   # a private repo asks for your GitHub username + a personal access token
fi
cd "$DIR"

if [ ! -f deploy/.env ]; then
  echo "==> creating deploy/.env"
  read -r -p "Your domain for Syena (e.g. syena.example.com): " DOMAIN </dev/tty
  read -r -p "Email for the HTTPS certificate notices: " EMAIL </dev/tty
  echo "Moving your laptop data here? Paste SYENA_ENCRYPTION_KEY from deploy\\syena-local.conf on the laptop"
  read -r -s -p "(or press Enter to start fresh with a new key): " ENC </dev/tty; echo
  if [ -z "$ENC" ]; then
    ENC=$(docker run --rm python:3.12-slim sh -c "pip -q install cryptography >/dev/null 2>&1 && python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'")
  fi
  cp deploy/.env.example deploy/.env
  sed -i "s|^SYENA_DOMAIN=.*|SYENA_DOMAIN=$DOMAIN|; s|^SYENA_ACME_EMAIL=.*|SYENA_ACME_EMAIL=$EMAIL|;
          s|^SYENA_PUBLIC_ORIGIN=.*|SYENA_PUBLIC_ORIGIN=https://$DOMAIN|;
          s|^POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD=$(openssl rand -hex 24)|;
          s|^BACKUP_PASSPHRASE=.*|BACKUP_PASSPHRASE=$(openssl rand -hex 32)|;
          s|^SYENA_ENCRYPTION_KEY=.*|SYENA_ENCRYPTION_KEY=$ENC|" deploy/.env
  chmod 600 deploy/.env
fi

IP=$(curl -fsS https://api.ipify.org || echo "<this server's IP>")
cat <<EOF

==> Server ready. Next steps:
 1. DNS: at your domain registrar add an A record:  $(grep '^SYENA_DOMAIN=' deploy/.env | cut -d= -f2)  ->  $IP
 2. Put your API keys in the settings file (only on this server, never in chat):
       nano $DIR/deploy/.env
    (Angel One, Anthropic, Telegram, Zerodha: the same values as on your laptop)
 3. Broker apps: register this static IP $IP in the Angel SmartAPI app (and Kite Connect app when you go live);
    Zerodha redirect URL: https://<your domain>/kite/callback
 4. Start Syena:   cd $DIR && ./deploy/deploy.sh
 5. First time only, create your login:
       docker compose -f deploy/docker-compose.prod.yml --env-file deploy/.env run --rm api syena create-admin
    or bring your laptop data:  ./deploy/import-laptop.sh <file from windows\\export-for-server.bat>
EOF
