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
  # frontend/dist 가 아니라 node_modules/.cache 에 빌드한다 — dist 는 이 컴퓨터의 실서버가 그대로 내보내는 폴더라,
  # 접두사(/ledger 등) 없이 덮어쓰면 실서비스 화면이 깨진다
  (cd "$ROOT/frontend" && npm run build --silent -- --outDir node_modules/.cache/test-dist --emptyOutDir >/dev/null)
fi

echo "[test] 모두 통과"
