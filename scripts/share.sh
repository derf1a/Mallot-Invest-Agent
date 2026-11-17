#!/usr/bin/env bash
# 인터넷에 열어 띄우기: 프론트 빌드 → 백엔드(127.0.0.1:8100, 로그인 필수) → Cloudflare 터널.
#   ~/.cloudflared/mallot-invest.yml 이 있으면 내 도메인(named tunnel — scripts/tunnel-setup.sh 로 만든다),
#   없으면 임시 주소(Quick Tunnel, https://…trycloudflare.com — 다시 켜면 바뀐다).
#   SHARE_TUNNEL=0 이면 서버만 띄운다 — 터널은 바깥(mallot-home 의 mallot-tunnel)이 맡을 때.
# 끄기: Ctrl+C (백엔드 · 터널을 같이 끈다). 기록: data/share.log, 지금 주소: data/share-url.txt
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PORT="${PORT:-8100}"
CONF="${TUNNEL_CONFIG:-$HOME/.cloudflared/mallot-invest.yml}"
mkdir -p "$ROOT/data"
# 경로 접두사(MIA_BASE_PATH, 예: /invest) — 셸 · systemd EnvironmentFile 이 우선, 없으면 .env 에서. 프론트 빌드와 백엔드가 같은 값을 쓴다
if [ -z "${MIA_BASE_PATH:-}" ] && [ -f "$ROOT/.env" ]; then
  MIA_BASE_PATH="$(sed -n 's/^MIA_BASE_PATH=//p' "$ROOT/.env" | tail -1 | tr -d "\"'")"
fi
export MIA_BASE_PATH="${MIA_BASE_PATH:-}"

# 홈 모드(MIA_HOME_AUTH_URL — 로그인은 Mallot 홈)면 앱 비밀번호가 없어도 된다
home_auth="${MIA_HOME_AUTH_URL:-}"
[ -z "$home_auth" ] && home_auth="$(sed -n 's/^MIA_HOME_AUTH_URL=//p' "$ROOT/.env" 2>/dev/null | tail -1 || true)"
if [ -z "$home_auth" ] && ! grep -qE '^MIA_ADMIN_PASSWORD(_HASH)?=.+' "$ROOT/.env" 2>/dev/null && [ -z "${MIA_ADMIN_PASSWORD_HASH:-}${MIA_ADMIN_PASSWORD:-}" ]; then
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
echo "[share] 서버 http://127.0.0.1:$PORT${MIA_BASE_PATH:+$MIA_BASE_PATH/} (로그인 필수${home_auth:+ — Mallot 홈 로그인})"

if [ "${SHARE_TUNNEL:-1}" = "0" ]; then
  echo "[share] 터널 없이 서버만 (SHARE_TUNNEL=0)"
elif [ -f "$CONF" ]; then
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
if [ -n "$home_auth" ]; then echo "[share] 로그인 · 손님 공유는 Mallot 홈 → 보안에서"; else echo "[share] 손님 링크는 관리자 화면 Settings > 공유 · 접근 에서"; fi
while for p in "${pids[@]}"; do kill -0 "$p" 2>/dev/null || exit 1; done; do sleep 2; done
