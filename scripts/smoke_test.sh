#!/usr/bin/env bash
# One command that proves iV actually works end to end.
#
#   scripts/smoke_test.sh              # boot the stack, test, shut it down
#   scripts/smoke_test.sh --no-boot    # test an already-running iV
#   scripts/smoke_test.sh --api-only   # skip the frontend proxy checks
#
# Exit codes are deliberately three-valued, because "the server is fine but
# has no model" is a different fact from "the server is broken", and a
# smoke test that collapses them tells you to debug the wrong thing:
#
#   0  PASS      — stack up, a real model answered a real goal
#   2  DEGRADED  — stack up and the HTTP contract holds, but no provider
#                  key is configured, so no model could answer
#   1  FAIL      — something is actually broken
#
# Written during the Phase 1 audit. Every check here was executed against a
# clean clone before being written down.
set -uo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

IV_PORT="${IV_PORT:-8024}"
FRONTEND_PORT="${FRONTEND_PORT:-4024}"
API="http://127.0.0.1:${IV_PORT}"
WEB="http://127.0.0.1:${FRONTEND_PORT}"
BOOT=1
API_ONLY=0
RUN_PID=""
FAILURES=0

for arg in "$@"; do
    case "$arg" in
        --no-boot)  BOOT=0 ;;
        --api-only) API_ONLY=1 ;;
        *) echo "unknown flag: $arg" >&2; exit 1 ;;
    esac
done

pass() { printf '  \033[32mPASS\033[0m  %s\n' "$1"; }
fail() { printf '  \033[31mFAIL\033[0m  %s\n' "$1"; FAILURES=$((FAILURES + 1)); }
info() { printf '  ----  %s\n' "$1"; }

cleanup() {
    if [ -n "$RUN_PID" ]; then
        echo
        echo "==> stopping the stack"
        kill -TERM "-$RUN_PID" 2>/dev/null || kill -TERM "$RUN_PID" 2>/dev/null || true
        wait "$RUN_PID" 2>/dev/null || true
    fi
}
trap cleanup EXIT

echo "==> iV smoke test"

# --- 0. preconditions ------------------------------------------------------
if [ ! -f backend/.env ]; then
    fail "backend/.env is missing — run scripts/setup-env.sh first"
    exit 1
fi
SECRET="$(grep '^API_ACCESS_SECRET=' backend/.env | tail -1 | cut -d= -f2- | tr -d '"'\''' | xargs)"
if [ -z "$SECRET" ]; then
    fail "API_ACCESS_SECRET is empty in backend/.env — run scripts/setup-env.sh"
    exit 1
fi

# --- 1. boot ---------------------------------------------------------------
if [ "$BOOT" = "1" ]; then
    echo "==> booting (this installs deps on a cold clone; can take a few minutes)"
    set -m
    if [ "$API_ONLY" = "1" ]; then ./run.sh --api-only >/tmp/iv-smoke-boot.log 2>&1 &
    else ./run.sh >/tmp/iv-smoke-boot.log 2>&1 & fi
    RUN_PID=$!
    set +m

    for _ in $(seq 1 180); do
        kill -0 "$RUN_PID" 2>/dev/null || { fail "run.sh exited during startup — see /tmp/iv-smoke-boot.log"; tail -20 /tmp/iv-smoke-boot.log; exit 1; }
        curl -fsS "${API}/health" >/dev/null 2>&1 && break
        sleep 1
    done
fi

# --- 2. API liveness -------------------------------------------------------
echo "==> API"
HEALTH="$(curl -fsS --max-time 10 "${API}/health" 2>/dev/null)"
if [ -z "$HEALTH" ]; then fail "GET /health unreachable at ${API}"; exit 1; fi
pass "GET /health reachable (unauthenticated, as designed)"

echo "$HEALTH" | grep -q '"persistence":{"ok":true' \
    && pass "persistence round-trip ok" \
    || fail "persistence check failed: $HEALTH"

MODEL_OK=0
echo "$HEALTH" | grep -q '"model_harness":{"ok":true' && MODEL_OK=1
if [ "$MODEL_OK" = "1" ]; then pass "a real model provider is configured"
else info "no real model provider configured (health reports degraded)"; fi

# --- 3. the secret actually gates ------------------------------------------
echo "==> auth"
CODE="$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 -X POST "${API}/api/chat" \
        -H 'Content-Type: application/json' -d '{"message":"smoke"}')"
[ "$CODE" = "403" ] && pass "POST /api/chat without a secret -> 403" \
                    || fail "POST /api/chat without a secret returned $CODE, expected 403"

CODE="$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 -X POST "${API}/api/chat" \
        -H 'Content-Type: application/json' -H 'X-API-Secret: definitely-not-the-secret' \
        -d '{"message":"smoke"}')"
[ "$CODE" = "403" ] && pass "POST /api/chat with a wrong secret -> 403" \
                    || fail "POST /api/chat with a wrong secret returned $CODE, expected 403"

# --- 4. submit one real goal -----------------------------------------------
echo "==> one real goal through /api/chat"
GOAL='List my projects, then tell me in one sentence what you did.'
BODY="$(curl -s --max-time 180 -X POST "${API}/api/chat" \
        -H 'Content-Type: application/json' -H "X-API-Secret: ${SECRET}" \
        -d "$(printf '{"message":%s}' "$(printf '%s' "$GOAL" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))')")")"

if [ -z "$BODY" ]; then fail "POST /api/chat returned an empty body"; exit 1; fi

CONV="$(printf '%s' "$BODY" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("conversation_id",""))' 2>/dev/null)"
RESP="$(printf '%s' "$BODY" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("response",""))' 2>/dev/null)"
USED="$(printf '%s' "$BODY" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("model_used") or "")' 2>/dev/null)"

[ -n "$CONV" ] && pass "response carries a conversation_id ($CONV)" \
               || fail "response has no conversation_id: $BODY"
[ -n "$RESP" ] && pass "response carries non-empty text" \
               || fail "response text is empty: $BODY"

case "$RESP" in
    *"couldn't reach any configured model"*)
        info "no model answered — iV reported no provider was reachable" ;;
    *)
        [ -n "$USED" ] && pass "a real model answered (model_used=${USED})" \
                       || info "answered, but model_used was null" ;;
esac

# --- 5. the turn was actually persisted ------------------------------------
echo "==> persistence of that turn"
HIST="$(curl -s --max-time 15 "${API}/api/conversations/${CONV}" -H "X-API-Secret: ${SECRET}")"
printf '%s' "$HIST" | grep -q '"role"' \
    && pass "GET /api/conversations/{id} replays the turn" \
    || fail "conversation history did not come back: $HIST"

DB="${IV_LOCAL_DB_PATH:-iv.db}"
if [ -f "$DB" ]; then
    N="$(python3 -c "
import sqlite3,sys
try:
    c=sqlite3.connect('$DB')
    print(c.execute(\"SELECT COUNT(*) FROM records WHERE collection='audit_log' AND json_extract(data,'\$.action')='chat.turn'\").fetchone()[0])
except Exception: print(0)
" 2>/dev/null)"
    [ "${N:-0}" -ge 1 ] && pass "audit_log has a chat.turn row (${N} total)" \
                        || fail "no chat.turn row was written to the audit log"
else
    info "no local db at $DB to inspect"
fi

# --- 6. frontend + proxy ---------------------------------------------------
if [ "$API_ONLY" != "1" ]; then
    echo "==> frontend"
    # run.sh prints "iV is up" as soon as it has *launched* next dev, not
    # when next dev can serve -- on a cold start Next compiles for another
    # 10-40s, during which the advertised URL refuses connections. Wait for
    # it here rather than reporting a failure that is really a race.
    for _ in $(seq 1 90); do
        curl -fsS -o /dev/null --max-time 5 "${WEB}/" 2>/dev/null && break
        sleep 1
    done
    CODE="$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 "${WEB}/")"
    [ "$CODE" = "200" ] && pass "frontend serves on ${WEB}" \
                        || fail "frontend returned $CODE at ${WEB}"

    curl -s --max-time 15 "${WEB}/api/iv/health" | grep -q '"status"' \
        && pass "proxy forwards /api/iv/health with a server-side secret" \
        || fail "proxy did not forward health"

    CODE="$(curl -s -o /dev/null -w '%{http_code}' --max-time 15 "${WEB}/api/iv/approvals/pending")"
    [ "$CODE" = "404" ] && pass "proxy allowlist blocks /approvals/* (404)" \
                        || fail "proxy exposed /approvals/* — got $CODE, expected 404"
fi

# --- verdict ---------------------------------------------------------------
echo
if [ "$FAILURES" -gt 0 ]; then
    echo "==> FAIL: ${FAILURES} check(s) failed"
    exit 1
fi
if [ "$MODEL_OK" != "1" ] || [ -z "$USED" ]; then
    echo "==> DEGRADED: the stack is healthy and the HTTP contract holds,"
    echo "    but no model provider answered. Add a provider key to backend/.env"
    echo "    (GROQ_API_KEY / GEMINI_API_KEY / ...) and re-run for a full pass."
    exit 2
fi
echo "==> PASS: iV is up and a real model completed a real goal."
exit 0
