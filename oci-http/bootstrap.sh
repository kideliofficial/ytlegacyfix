#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "Usage: sudo bash oci-http/bootstrap.sh <public-ip>" >&2
  exit 2
fi

PUBLIC_IPV4="$1"
if [[ ! "$PUBLIC_IPV4" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "Expected the VM's public IPv4 address." >&2
  exit 2
fi

REPO_DIR="/opt/ytlegacyfix"
apt-get update
apt-get install -y docker.io git
systemctl enable --now docker

if [[ -d "$REPO_DIR/.git" ]]; then
  git -C "$REPO_DIR" pull --ff-only
else
  git clone https://github.com/kideliofficial/ytlegacyfix.git "$REPO_DIR"
fi

docker build -f "$REPO_DIR/oci-http/Dockerfile" -t ytlegacyfix "$REPO_DIR"
docker rm -f ytlegacyfix >/dev/null 2>&1 || true
docker run -d --name ytlegacyfix --restart unless-stopped \
  -p 80:8080 \
  -e "YTLF_PUBLIC_BASE=http://$PUBLIC_IPV4" \
  -e YTLF_LOG=/dev/null \
  -e YTLF_SAVE_DIR= \
  -e 'YTLF_UA=com.google.ios.youtube/21.26.4 (iPhone16,2; U; CPU iOS 18_3_2 like Mac OS X;)' \
  -e YTLF_DROP_HEADERS=X-Goog-Device-Auth \
  -e YTLF_PB_DROP_MS=1 \
  -e YTLF_PB_VERSION=21.26.4 \
  -e YTLF_PB_OSVER=18.3.2.22D82 \
  ytlegacyfix

echo "Proxy: http://$PUBLIC_IPV4/healthz"
echo "Cydia source: http://$PUBLIC_IPV4/cydia/"
