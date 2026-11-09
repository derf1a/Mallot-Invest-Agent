#!/usr/bin/env bash
# 비밀값 검사 + 백엔드 pytest + 프론트 oxlint·타입체크·빌드
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

echo "[test] secrets"
"$ROOT/scripts/check-secrets.sh"

echo "[test] backend: pytest"
(cd "$ROOT/backend" && uv run pytest -q "$@")

if [ -d "$ROOT/frontend" ]; then
  echo "[test] frontend: lint"
  (cd "$ROOT/frontend" && npm run lint --silent)
  echo "[test] frontend: typecheck + build"
  (cd "$ROOT/frontend" && npm run build --silent >/dev/null)
fi

echo "[test] 모두 통과"
