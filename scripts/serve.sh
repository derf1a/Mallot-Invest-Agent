#!/usr/bin/env bash
# 프론트를 빌드한 뒤 백엔드 하나로 서빙한다. 기본은 이 컴퓨터에서만 (127.0.0.1).
# 같은 네트워크의 폰에서 열려면 HOST=0.0.0.0 — 로그인이 없는 앱이라 믿는 네트워크에서만.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PORT="${PORT:-8100}"
HOST="${HOST:-127.0.0.1}"
# 경로 접두사(MIA_BASE_PATH, 예: /invest) — 셸 · systemd EnvironmentFile 이 우선, 없으면 .env 에서. 프론트 빌드와 백엔드가 같은 값을 쓴다
if [ -z "${MIA_BASE_PATH:-}" ] && [ -f "$ROOT/.env" ]; then
  MIA_BASE_PATH="$(sed -n 's/^MIA_BASE_PATH=//p' "$ROOT/.env" | tail -1 | tr -d "\"'")"
fi
export MIA_BASE_PATH="${MIA_BASE_PATH:-}"

if [ -d "$ROOT/frontend" ]; then (cd "$ROOT/frontend" && npm run build); fi
echo "[serve] http://localhost:$PORT${MIA_BASE_PATH:+$MIA_BASE_PATH/}"
cd "$ROOT/backend" && exec uv run uvicorn app.main:app --host "$HOST" --port "$PORT"
