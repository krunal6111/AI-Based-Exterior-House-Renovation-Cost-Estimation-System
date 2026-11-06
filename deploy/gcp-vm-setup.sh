#!/usr/bin/env bash
# One-time (and re-run-safe) setup for the backend on a fresh Ubuntu GCP VM.
#
# Run as root on the VM itself (e.g. right after `gcloud compute ssh`):
#   sudo bash gcp-vm-setup.sh <your-domain> [git-branch]
#
# <your-domain> must already resolve (A record) to this VM's external IP --
# Caddy requests a Let's Encrypt certificate for it on first start and that
# fails until DNS is live. git-branch defaults to "main".
set -euo pipefail

DOMAIN="${1:?Usage: gcp-vm-setup.sh <domain> [git-branch]}"
BRANCH="${2:-main}"
REPO_URL="https://github.com/krunal6111/AI-Based-Exterior-House-Renovation-Cost-Estimation-System.git"
APP_DIR=/opt/reno/app

if [[ $EUID -ne 0 ]]; then
  echo "Run this as root (sudo bash gcp-vm-setup.sh ...)" >&2
  exit 1
fi

if [[ ! -f /swapfile ]]; then
  echo "==> Adding a 2GB swap file (e2-micro has only 1GB RAM; the CV/image"
  echo "    pipeline can spike well past that on larger photos)"
  fallocate -l 2G /swapfile
  chmod 600 /swapfile
  mkswap /swapfile
  swapon /swapfile
  echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

echo "==> Installing system packages"
apt-get update -y
apt-get install -y python3-venv python3-pip git curl ca-certificates gnupg debian-keyring debian-archive-keyring apt-transport-https

if ! command -v caddy >/dev/null 2>&1; then
  echo "==> Installing Caddy"
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | tee /etc/apt/sources.list.d/caddy-stable.list
  apt-get update -y
  apt-get install -y caddy
fi

if ! id -u reno >/dev/null 2>&1; then
  echo "==> Creating service user 'reno'"
  useradd --system --create-home --home-dir /opt/reno --shell /usr/sbin/nologin reno
fi
mkdir -p /opt/reno
chown reno:reno /opt/reno

if [[ -d "$APP_DIR/.git" ]]; then
  echo "==> Repo already present, pulling latest $BRANCH"
  sudo -u reno git -C "$APP_DIR" fetch origin "$BRANCH"
  sudo -u reno git -C "$APP_DIR" checkout "$BRANCH"
  sudo -u reno git -C "$APP_DIR" pull --ff-only origin "$BRANCH"
else
  echo "==> Cloning repo"
  sudo -u reno git clone --branch "$BRANCH" "$REPO_URL" "$APP_DIR"
fi

echo "==> Python virtualenv + dependencies"
sudo -u reno python3 -m venv "$APP_DIR/backend/.venv"
sudo -u reno "$APP_DIR/backend/.venv/bin/pip" install --upgrade pip
sudo -u reno "$APP_DIR/backend/.venv/bin/pip" install -r "$APP_DIR/backend/requirements.txt"
sudo -u reno "$APP_DIR/backend/.venv/bin/pip" install -r "$APP_DIR/backend/requirements-ai.txt"

mkdir -p "$APP_DIR/storage"
chown -R reno:reno "$APP_DIR/storage"

if [[ ! -f "$APP_DIR/backend/.env" ]]; then
  echo "==> No backend/.env yet -- seeding one from .env.example"
  sudo -u reno cp "$APP_DIR/backend/.env.example" "$APP_DIR/backend/.env"
  echo "    EDIT $APP_DIR/backend/.env now: set VERTEX_PROJECT or GEMINI_API_KEY,"
  echo "    GOOGLE_APPLICATION_CREDENTIALS, and CORS_ORIGINS before going further."
fi

echo "==> systemd service"
cp "$APP_DIR/deploy/reno-backend.service" /etc/systemd/system/reno-backend.service
systemctl daemon-reload
systemctl enable --now reno-backend
systemctl restart reno-backend

echo "==> Caddy reverse proxy for $DOMAIN"
sed "s/__DOMAIN__/$DOMAIN/" "$APP_DIR/deploy/Caddyfile.template" > /etc/caddy/Caddyfile
systemctl enable caddy
systemctl restart caddy

echo ""
echo "Done. Check status with:"
echo "  systemctl status reno-backend"
echo "  systemctl status caddy"
echo "  journalctl -u reno-backend -f"
echo ""
echo "If backend/.env was just created, edit it (nano $APP_DIR/backend/.env),"
echo "copy vertex-key.json onto this VM if you're using Vertex AI, then:"
echo "  systemctl restart reno-backend"
