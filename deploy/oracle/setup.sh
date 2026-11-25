#!/usr/bin/env bash
# One-time setup of the Kairo backend on a fresh Oracle Cloud VM, either Ubuntu
# (log in as ubuntu) or Oracle Linux, the console's default image (log in as opc).
#
#   bash setup.sh <public IP of the VM>
#
# Installs Docker from Docker's own repository, opens ports 80 and 443 in the VM's
# firewall, clones the repo to ~/kairo and creates .env.production for you to fill
# in. Safe to run again.
set -euo pipefail

IP="${1:?Usage: bash setup.sh <public IP of the VM>}"
REPO_URL="${REPO_URL:-https://github.com/JefferyLiu6/Kairo.git}"
DIR="${KAIRO_DIR:-$HOME/kairo}"
API_DOMAIN="${IP//./-}.sslip.io"

if command -v apt-get >/dev/null 2>&1; then OS=ubuntu
elif command -v dnf >/dev/null 2>&1; then OS=oracle-linux
else echo "This script supports Ubuntu and Oracle Linux only." >&2; exit 1
fi

echo "==> Installing Docker"
if ! command -v docker >/dev/null 2>&1; then
  if [ "$OS" = ubuntu ]; then
    sudo apt-get update
    sudo apt-get install -y ca-certificates curl git
    sudo install -m 0755 -d /etc/apt/keyrings
    sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
    sudo chmod a+r /etc/apt/keyrings/docker.asc
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] \
https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" |
      sudo tee /etc/apt/sources.list.d/docker.list >/dev/null
    sudo apt-get update
    sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
  else
    sudo dnf install -y dnf-plugins-core git
    sudo dnf config-manager --add-repo https://download.docker.com/linux/centos/docker-ce.repo
    # --allowerasing replaces podman's runc and podman-docker if the image has them.
    sudo dnf install -y --allowerasing docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
  fi
  sudo systemctl enable --now docker
  sudo usermod -aG docker "$USER"
fi

echo "==> Opening ports 80 and 443 in the VM firewall"
if [ "$OS" = oracle-linux ]; then
  # Oracle Linux guards the VM with firewalld, on top of the cloud security list.
  if systemctl is-active --quiet firewalld; then
    sudo firewall-cmd --permanent --add-service=http --add-service=https >/dev/null
    sudo firewall-cmd --permanent --add-port=443/udp >/dev/null
    sudo firewall-cmd --reload >/dev/null
  fi
else
  # Oracle's Ubuntu images reject everything but SSH in iptables, on top of the
  # cloud security list. Insert the rules before that REJECT rule and keep them.
  for port in 80 443; do
    if ! sudo iptables -C INPUT -p tcp --dport "$port" -m state --state NEW -j ACCEPT 2>/dev/null; then
      sudo iptables -I INPUT 5 -p tcp --dport "$port" -m state --state NEW -j ACCEPT
    fi
  done
  if ! sudo iptables -C INPUT -p udp --dport 443 -j ACCEPT 2>/dev/null; then
    sudo iptables -I INPUT 5 -p udp --dport 443 -j ACCEPT
  fi
  if command -v netfilter-persistent >/dev/null 2>&1; then
    sudo netfilter-persistent save
  fi
fi

echo "==> Getting the code"
if [ -d "$DIR/.git" ]; then
  git -C "$DIR" pull --ff-only
else
  git clone "$REPO_URL" "$DIR"
fi

ENV_FILE="$DIR/.env.production"
if [ ! -f "$ENV_FILE" ]; then
  cp "$DIR/.env.production.example" "$ENV_FILE"
  chmod 600 "$ENV_FILE"
  {
    echo
    echo "# ── Oracle VM ──"
    echo "API_DOMAIN=$API_DOMAIN"
  } >>"$ENV_FILE"
  sed -i "s#^PUBLIC_URL=.*#PUBLIC_URL=https://$API_DOMAIN#" "$ENV_FILE"
fi

cat <<EOF

Done. Next:
  1. Log out and back in (so your user can run docker without sudo).
  2. Fill in $ENV_FILE: copy every value from the Render backend's
     Environment page. Keep PUBLIC_URL=https://$API_DOMAIN and API_DOMAIN as set.
  3. Start it:
       cd $DIR && bash deploy/oracle/deploy.sh
  4. Check https://$API_DOMAIN/health in a browser.
EOF
