#!/usr/bin/env bash
# 공개 저장소 지킴이: 커밋하려는 파일에 비밀값·개인 데이터가 섞였는지 본다.
#   scripts/check-secrets.sh          → 추적 중인 파일 전체
#   scripts/check-secrets.sh --staged → 이번 커밋에 올라간 파일만 (pre-commit 훅)
set -euo pipefail
cd "$(dirname "$0")/.."

if [ "${1:-}" = "--staged" ]; then
  files=$(git diff --cached --name-only --diff-filter=ACMR)
else
  files=$(git ls-files)
fi
[ -z "$files" ] && exit 0

fail=0
# 1) 들어가면 안 되는 파일 (DB · .env · data 폴더 · 모델 · 키 파일)
bad=$(printf '%s\n' "$files" | grep -E '(^|/)(data|backups|models|secrets)/|\.(db|sqlite3?|pem|key)$|(^|/)\.env($|\.)' | grep -v '\.env\.example$' || true)
if [ -n "$bad" ]; then
  echo "[check-secrets] 커밋하면 안 되는 파일:"; printf '  %s\n' $bad; fail=1
fi

# 2) 값이 채워진 키 · 토큰 모양
patterns='sk-ant-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{30,}|AKIA[0-9A-Z]{16}|-----BEGIN [A-Z ]*PRIVATE KEY-----|lsv2_[a-z]{2}_[A-Za-z0-9]{20,}|(TOSS_CLIENT_SECRET|TOSS_CLIENT_ID|ANTHROPIC_API_KEY|DART_API_KEY|LANGSMITH_API_KEY|MIA_TRADING_PIN|MIA_VLLM_API_KEY)=[^[:space:]$"'"'"']+'
for f in $files; do
  [ -f "$f" ] || continue
  case "$f" in scripts/check-secrets.sh) continue ;; esac
  if [ "${1:-}" = "--staged" ]; then
    hits=$(git show ":$f" 2>/dev/null | grep -nEI "$patterns" || true)
  else
    hits=$(grep -nEI "$patterns" "$f" || true)
  fi
  if [ -n "$hits" ]; then
    echo "[check-secrets] 키처럼 보이는 값: $f"; printf '%s\n' "$hits" | cut -c1-120 | sed 's/^/  /'; fail=1
  fi
done

if [ "$fail" -ne 0 ]; then
  echo "[check-secrets] 막았어요. 값을 지우거나 .gitignore 에 넣은 뒤 다시 커밋하세요."
  exit 1
fi
echo "[check-secrets] 통과"
