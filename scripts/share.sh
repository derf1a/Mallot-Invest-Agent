#!/usr/bin/env bash
# 인터넷에 열어 띄우기: 프론트 빌드 → 백엔드(127.0.0.1:8100, 로그인 필수) → Cloudflare 터널.
#   ~/.cloudflared/mallot-invest.yml 이 있으면 내 도메인(named tunnel — scripts/tunnel-setup.sh 로 만든다),
#   없으면 임시 주소(Quick Tunnel, https://…trycloudflare.com — 다시 켜면 바뀐다).
# 끄기: Ctrl+C (백엔드 · 터널을 같이 끈다). 기록: data/share.log, 지금 주소: data/share-url.txt
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PORT="${PORT:-8100}"
CONF="${TUNNEL_CONFIG:-$HOME/.cloudflared/mallot-invest.yml}"
mkdir -p "$ROOT/data"

if ! grep -qE '^MIA_ADMIN_PASSWORD(_HASH)?=.+' "$ROOT/.env" 2>/dev/null && [ -z "${MIA_ADMIN_PASSWORD_HASH:-}${MIA_ADMIN_PASSWORD:-}" ]; then
  echo "[share] 관리자 비밀번호가 없어요 — 먼저: cd backend && uv run python -m app.auth.passwd" >&2
  exit 1
fi
command -v cloudflared >/dev/null || { echo "[share] cloudflared 가 없어요 — brew install cloudflared" >&2; exit 1; }

(cd "$ROOT/frontend" && npm run build --silent >/dev/null)

pids=()
cleanup() {
  trap - INT TERM EXIT
  for pid in "${pids[@]}"; do kill "$pid" 2>/dev/null || true; done
  wait 2>/dev/null || true
  rm -f "$ROOT/data/share-url.txt"
}
trap cleanup INT TERM EXIT

(cd "$ROOT/backend" && MIA_PUBLIC=1 exec uv run uvicorn app.main:app --host 127.0.0.1 --port "$PORT") &
pids+=($!)
for _ in $(seq 1 60); do curl -fsS "http://127.0.0.1:$PORT/api/health" >/dev/null 2>&1 && break; sleep 1; done
echo "[share] 서버 http://127.0.0.1:$PORT (로그인 필수)"

if [ -f "$CONF" ]; then
  host=$(awk '/hostname:/ {print $NF; exit}' "$CONF")
  echo "https://$host" > "$ROOT/data/share-url.txt"
  echo "[share] 내 도메인 https://$host"
  cloudflared tunnel --no-autoupdate --config "$CONF" run &
  pids+=($!)
else
  log="$ROOT/data/tunnel.log"
  : > "$log"
  cloudflared tunnel --no-autoupdate --url "http://127.0.0.1:$PORT" > "$log" 2>&1 &
  pids+=($!)
  for _ in $(seq 1 60); do
    url=$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' "$log" | head -1 || true)
    [ -n "$url" ] && break
    sleep 1
  done
  if [ -n "${url:-}" ]; then
    echo "$url" > "$ROOT/data/share-url.txt"
    echo "[share] 임시 주소 $url (다시 켜면 바뀜 — 고정 주소는 docs/share.md)"
  else
    echo "[share] 임시 주소를 받지 못했어요 — $log 를 보세요" >&2
  fi
fi
echo "[share] 손님 링크는 관리자 화면 Settings > 공유 · 접근 에서"
while for p in "${pids[@]}"; do kill -0 "$p" 2>/dev/null || exit 1; done; do sleep 2; done
