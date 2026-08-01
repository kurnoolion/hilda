#!/usr/bin/env bash
#
# unrouted_smoke_test.sh -- read-only smoke test for the /_unknownTG UI.
#
# Ph-2 architect ask 2026-08-01 (UR-9). Verifies routing + template render
# after the UR-1..8 rollout. Read-only by design -- the POST /route path
# needs a real unrouted doc to test end-to-end, which requires either
# waiting for one to appear naturally or seeding one via SQL (see the
# "Manual POST verification" section at the bottom of the script).
#
# Usage (from the staging PC):
#   HILDA_BASE=https://hilda.corp.internal:8443 bash scripts/unrouted_smoke_test.sh
#
# Or without HILDA_BASE, defaults to the localhost hilda-api:
#   bash scripts/unrouted_smoke_test.sh
#
# Exit 0 on all-green, non-zero on first failure with a diagnostic message.

set -euo pipefail

: "${HILDA_BASE:=https://localhost:8443}"
: "${CUSTOMER:=MMK}"
: "${DEVICE:=SM-A012U}"
: "${MILESTONE:=DRR}"

BASE="${HILDA_BASE%/}"
SCOPE="${CUSTOMER}/${DEVICE}/${MILESTONE}"

log()  { printf "\033[1;34m[ur-smoke]\033[0m %s\n" "$*"; }
pass() { printf "\033[1;32m  ok\033[0m: %s\n" "$*"; }
fail() { printf "\033[1;31m  FAIL\033[0m: %s\n" "$*" >&2; exit 1; }

log "target: ${BASE}   scope: ${SCOPE}"

# 1. Landing page renders. Bucket row for _unknownTG is always shown per
#    UR-7 -- even with zero unrouted files the row appears so TPMs learn
#    where triage lives.
log "1/3 GET /browse/${SCOPE}/  (expect 200 + _unknownTG bucket row)"
BODY=$(curl -k -s -w "\n__HTTPCODE__%{http_code}" "${BASE}/browse/${SCOPE}/")
CODE="${BODY##*__HTTPCODE__}"
BODY="${BODY%__HTTPCODE__*}"
[[ "$CODE" == "200" ]] || fail "landing page expected 200, got $CODE"
grep -q "_unknownTG" <<< "$BODY" || fail "landing page missing _unknownTG bucket row"
grep -q "/browse/${SCOPE}/_unknownTG/" <<< "$BODY" \
    || fail "landing page missing link to /_unknownTG/"
pass "landing renders bucket row + link"

# 2. /_unknownTG/ triage page renders. Two cases are both valid:
#    - "No unrouted files" (empty state) when the bucket is empty
#    - Table with dropdown when unrouted docs exist
log "2/3 GET /browse/${SCOPE}/_unknownTG/  (expect 200; either empty or table)"
BODY=$(curl -k -s -w "\n__HTTPCODE__%{http_code}" "${BASE}/browse/${SCOPE}/_unknownTG/")
CODE="${BODY##*__HTTPCODE__}"
BODY="${BODY%__HTTPCODE__*}"
[[ "$CODE" == "200" ]] || fail "triage page expected 200, got $CODE"
if grep -q "No unrouted files" <<< "$BODY"; then
    pass "triage page renders empty state (0 unrouted)"
elif grep -q "Route to work item" <<< "$BODY"; then
    pass "triage page renders table with routing dropdown"
else
    fail "triage page neither empty state nor table -- template broken?"
fi

# 3. Empty POST -> 422 (FastAPI Form validation). Confirms the route is
#    reachable + Form parser is loaded even before any real routing.
log "3/3 POST /browse/${SCOPE}/_unknownTG/route with empty body (expect 422)"
CODE=$(curl -k -s -o /dev/null -w "%{http_code}" \
    -X POST "${BASE}/browse/${SCOPE}/_unknownTG/route")
case "$CODE" in
    422)  pass "empty POST validated (422; route reachable + multipart loaded)" ;;
    500)  fail "500 -- likely python-multipart missing (pip install python-multipart)" ;;
    *)    fail "expected 422, got $CODE" ;;
esac

echo
echo "\033[1;32mAll checks passed.\033[0m"
echo
echo "----------------------------------------------------------------------"
echo "Manual POST verification (only after a real unrouted doc lands):"
echo "----------------------------------------------------------------------"
echo "1. Wait until an inbound email routes something to _unrouted OR seed"
echo "   one manually in postgres."
echo "2. Open ${BASE}/browse/${SCOPE}/_unknownTG/ in a browser."
echo "3. Pick a target work item from the dropdown, click Route."
echo "4. Verify the green 'Routed to <item>' banner + the doc row disappears."
echo "5. Check the audit log for a 'manual_route_from_unrouted' entry:"
echo
echo "   docker exec hilda-postgres psql -U hilda -c \\"
echo "     \"SELECT log_id, action_type, summary FROM communication_log \\"
echo "      WHERE action_type='manual_route_from_unrouted' \\"
echo "      ORDER BY timestamp DESC LIMIT 5;\""
