#!/usr/bin/env bash
# One-time: creates a 'deploy' user for GitHub Actions to SSH in as, locked
# down to running exactly one command as root (deploy/redeploy.sh) via
# sudo -- the CI private key never grants more than that.
#
# Run as root on the VM:
#   sudo bash create-deploy-user.sh "ssh-ed25519 AAAA... github-actions"
set -euo pipefail

PUBKEY="${1:?Usage: create-deploy-user.sh \"<public key>\"}"

if ! id -u deploy >/dev/null 2>&1; then
  useradd --create-home --shell /bin/bash deploy
fi

install -d -m 700 -o deploy -g deploy /home/deploy/.ssh
echo "$PUBKEY" > /home/deploy/.ssh/authorized_keys
chmod 600 /home/deploy/.ssh/authorized_keys
chown deploy:deploy /home/deploy/.ssh/authorized_keys

cat > /etc/sudoers.d/deploy <<'EOF'
deploy ALL=(root) NOPASSWD: /opt/reno/app/deploy/redeploy.sh
EOF
chmod 440 /etc/sudoers.d/deploy
visudo -c -f /etc/sudoers.d/deploy

echo "Done. deploy@<this-vm> can now run exactly:"
echo "  sudo /opt/reno/app/deploy/redeploy.sh"
echo "and nothing else with elevated rights."
