#!/usr/bin/env bash
# Pulls the latest main and restarts the backend. Meant to be run as root --
# the GitHub Actions workflow SSHes in as a limited 'deploy' user and runs
# this one script via a scoped sudoers rule, so it can both write as 'reno'
# and restart the systemd service without the CI key having full root.
set -euo pipefail

APP_DIR=/opt/reno/app

sudo -u reno git -C "$APP_DIR" fetch origin main
sudo -u reno git -C "$APP_DIR" reset --hard origin/main

sudo -u reno "$APP_DIR/backend/.venv/bin/pip" install -q -r "$APP_DIR/backend/requirements.txt"
sudo -u reno "$APP_DIR/backend/.venv/bin/pip" install -q -r "$APP_DIR/backend/requirements-ai.txt"

systemctl restart reno-backend
