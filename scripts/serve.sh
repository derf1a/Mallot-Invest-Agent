#!/usr/bin/env bash
# 프론트를 빌드한 뒤 백엔드 하나로 서빙한다. 기본은 이 컴퓨터에서만 (127.0.0.1).
# 같은 네트워크의 폰에서 열려면 HOST=0.0.0.0 — 로그인이 없는 앱이라 믿는 네트워크에서만.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PORT="${PORT:-8100}"
HOST="${HOST:-127.0.0.1}"

if [ -d "$ROOT/frontend" ]; then (cd "$ROOT/frontend" && npm run build); fi
echo "[serve] http://localhost:$PORT"
cd "$ROOT/backend" && exec uv run uvicorn app.main:app --host "$HOST" --port "$PORT"
