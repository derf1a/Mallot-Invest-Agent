#!/usr/bin/env bash
# 내 도메인으로 고정 주소 만들기 (한 번만): Cloudflare 터널 만들기 → DNS 연결 → 설정 파일.
#   먼저 브라우저 인증: cloudflared tunnel login   (도메인이 Cloudflare 에 있어야 한다)
#   그다음: scripts/tunnel-setup.sh invest.example.com
set -euo pipefail
HOST="${1:?사용법: scripts/tunnel-setup.sh <hostname, 예: invest.example.com>}"
NAME="${TUNNEL_NAME:-mallot-invest}"
PORT="${PORT:-8100}"
CONF="$HOME/.cloudflared/mallot-invest.yml"

[ -f "$HOME/.cloudflared/cert.pem" ] || { echo "먼저 cloudflared tunnel login 으로 인증하세요" >&2; exit 1; }
if ! cloudflared tunnel info "$NAME" >/dev/null 2>&1; then
  cloudflared tunnel create "$NAME"
fi
ID=$(cloudflared tunnel list -o json | python3 -c 'import json,sys; n=sys.argv[1]; print(next(t["id"] for t in json.load(sys.stdin) if t["name"]==n))' "$NAME")
cloudflared tunnel route dns "$NAME" "$HOST"
cat > "$CONF" <<CONF
# scripts/tunnel-setup.sh 가 만든 설정 — Mallot Invest 를 https://$HOST 로
tunnel: $ID
credentials-file: $HOME/.cloudflared/$ID.json
ingress:
  - hostname: $HOST
    service: http://127.0.0.1:$PORT
  - service: http_status:404
CONF
echo "만들었어요: $CONF → https://$HOST"
echo "이제 make share 로 띄우면 이 주소로 열려요."
