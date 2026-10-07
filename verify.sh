#!/usr/bin/env bash
# End-to-end verification for the Watermarks Remover Manual web UI.
# Usage: ./verify.sh
set -uo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
APP="$ROOT/app"
HOST="127.0.0.1"
PORT="${PORT:-8770}"
BASE="http://$HOST:$PORT"
PASS=0
FAIL=0

ok()   { echo "PASS: $1"; PASS=$((PASS+1)); }
bad()  { echo "FAIL: $1"; FAIL=$((FAIL+1)); }

check() { # name, condition-result(0=ok)
  if [ "$2" -eq 0 ]; then ok "$1"; else bad "$1"; fi
}

echo "== starting server =="
PORT="$PORT" HOST="$HOST" python3 "$APP/server.py" >/tmp/opencode/wm-web.log 2>&1 &
SRV=$!
trap 'kill "$SRV" 2>/dev/null' EXIT
for i in $(seq 1 30); do
  curl -sf "$BASE/api/health" >/dev/null 2>&1 && break
  sleep 0.3
done

echo "== health =="
curl -sf "$BASE/api/health" | grep -q '"ok": *true'; check "GET /api/health" $?

echo "== index + assets =="
curl -sf "$BASE/" | grep -qi "<html"; check "GET / returns HTML" $?
curl -sf "$BASE/assets/styles.css" >/dev/null; check "GET /assets/styles.css" $?
curl -sf "$BASE/assets/app.js" >/dev/null; check "GET /assets/app.js" $?

echo "== inspect text (U+200B) =="
R=$(curl -s -X POST "$BASE/api/inspect" -H 'Content-Type: application/json' \
  --data-binary '{"text":"Hello\u200bWorld"}')
echo "$R" | grep -q '"suspicious": *true'; check "inspect marks suspicious" $?
echo "$R" | grep -q '"kind": *"text"'; check "inspect kind=text" $?

echo "== clean text =="
R=$(curl -s -X POST "$BASE/api/clean" -H 'Content-Type: application/json' \
  --data-binary '{"text":"Hello\u200bWorld\u00ad! caf\u00e9\u00a0fin"}')
echo "$R" | grep -q '"ok": *true'; check "clean ok" $?
if echo "$R" | python3 -c 'import sys,json; d=json.load(sys.stdin); assert d.get("cleaned") is not None and "\u200b" not in d["cleaned"] and "\u00ad" not in d["cleaned"]'; then ok "cleaned removes U+200B/U+00AD"; else bad "cleaned removes U+200B/U+00AD"; fi

echo "== clean file (png) =="
PNG="iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0lEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
R=$(curl -s -X POST "$BASE/api/clean" -H 'Content-Type: application/json' \
  --data-binary "{\"filename\":\"t.png\",\"file_base64\":\"$PNG\"}")
echo "$R" | grep -q '"ok": *true'; check "clean file ok" $?
if echo "$R" | python3 -c 'import sys,json,base64; d=json.load(sys.stdin); assert d.get("cleaned_base64"); base64.b64decode(d["cleaned_base64"]); assert d.get("kind")=="image"'; then ok "png returns decodable cleaned_base64"; else bad "png returns decodable cleaned_base64"; fi

echo "== unknown format rejected =="
UB=$(printf 'randombytes\x00\x01\x02' | base64 -w0)
CODE=$(curl -s -o /tmp/opencode/wm-unknown.json -w '%{http_code}' -X POST "$BASE/api/clean" \
  -H 'Content-Type: application/json' \
  --data-binary "{\"filename\":\"u.bin\",\"file_base64\":\"$UB\"}")
[ "$CODE" = "422" ]; check "unknown -> HTTP 422" $?
grep -q '"ok": *false' /tmp/opencode/wm-unknown.json; check "unknown -> ok:false" $?

echo "== js syntax =="
if command -v node >/dev/null; then node --check "$APP/static/app.js"; check "node --check app.js" $?; fi

echo
echo "RESULT: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
