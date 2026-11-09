#!/usr/bin/env bash
# 개발 모드: 백엔드(--reload)와 프론트(Vite)를 함께 띄운다. Ctrl+C 한 번으로 둘 다 끝난다.
# Mallot(8000·3000)과 같이 띄워도 겹치지 않게 기본 포트를 8100·3100 으로 둔다.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export BACKEND_PORT="${BACKEND_PORT:-8100}"
export FRONTEND_PORT="${FRONTEND_PORT:-3100}"

pids=()
cleanup() {
  trap - INT TERM EXIT
  for pid in "${pids[@]}"; do kill "$pid" 2>/dev/null || true; done
  wait 2>/dev/null || true
}
trap cleanup INT TERM EXIT

(cd "$ROOT/backend" && exec uv run uvicorn app.main:app --reload --host 127.0.0.1 --port "$BACKEND_PORT") &
pids+=($!)
if [ -d "$ROOT/frontend" ]; then
  (cd "$ROOT/frontend" && exec npm run dev -- --port "$FRONTEND_PORT" --strictPort) &
  pids+=($!)
  echo "[dev] frontend http://localhost:$FRONTEND_PORT"
fi
echo "[dev] backend  http://localhost:$BACKEND_PORT"
while for p in "${pids[@]}"; do kill -0 "$p" 2>/dev/null || exit 1; done; do sleep 1; done
