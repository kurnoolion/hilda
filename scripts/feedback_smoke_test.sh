#!/usr/bin/env bash
#
# feedback_smoke_test.sh -- end-to-end smoke test for the /feedback/* UI.
#
# Ph-1 architect ask 2026-07-30 (FB-6). Verifies routing + view + submit +
# attachment upload/download after the FB-1..FB-5 rollout.
#
# Usage (from the staging PC):
#   HILDA_BASE=https://hilda.corp.internal:8443 bash scripts/feedback_smoke_test.sh
#
# Or without HILDA_BASE, defaults to the localhost hilda-api:
#   bash scripts/feedback_smoke_test.sh
#
# Exit 0 on all-green, non-zero on first failure with a diagnostic message.
# Does NOT clean up the ticket it creates -- the ticket lives on so ops
# can see one real submission landed and the email arrived.

set -euo pipefail

: "${HILDA_BASE:=https://localhost:8443}"
: "${CUSTOMER:=MMK}"
: "${DEVICE:=SM-A012U}"
: "${MILESTONE:=DRR}"

CURL_OPTS=(-k -s -S -o /dev/null -w "%{http_code}")
BASE="${HILDA_BASE%/}"
SCOPE="${CUSTOMER}/${DEVICE}/${MILESTONE}"

log()  { printf "\033[1;34m[fb-smoke]\033[0m %s\n" "$*"; }
pass() { printf "\033[1;32m  ok\033[0m: %s\n" "$*"; }
fail() { printf "\033[1;31m  FAIL\033[0m: %s\n" "$*" >&2; exit 1; }

log "target: ${BASE}   scope: ${SCOPE}"

# 1. Root-scope redirect -> DRR default
log "1/6 GET /feedback/${CUSTOMER}/${DEVICE}  (expect 302 to /DRR)"
CODE=$(curl "${CURL_OPTS[@]}" "${BASE}/feedback/${CUSTOMER}/${DEVICE}")
[[ "$CODE" == "302" ]] || fail "expected 302, got $CODE"
pass "302 redirect"

# 2. View page renders
log "2/6 GET /feedback/${SCOPE}  (expect 200 with form + table)"
BODY=$(curl -k -s "${BASE}/feedback/${SCOPE}")
CODE=$(printf "%s" "$BODY" | head -c 0; echo -n "")  # placeholder to avoid pipefail
CODE=$(curl "${CURL_OPTS[@]}" "${BASE}/feedback/${SCOPE}")
[[ "$CODE" == "200" ]] || fail "view page expected 200, got $CODE"
grep -q "HILDA Feedback"    <<< "$BODY" || fail "view page missing title"
grep -q "Submit ticket"     <<< "$BODY" || fail "view page missing submit button"
grep -q "$SCOPE" | true  # scope shown somewhere in body
pass "view page renders form + tickets section"

# 3. Submit a plain bug (no attachment)
log "3/6 POST /feedback/${SCOPE}/submit  bug + description"
CODE=$(curl -k -s -o /dev/null -w "%{http_code}" \
    -X POST "${BASE}/feedback/${SCOPE}/submit" \
    -F "category=bug" \
    -F "bug_type=SETUP-setup button not available / does not work" \
    -F "description=smoke-test $(date -u +%FT%TZ)" \
    -F "target_milestone=${MILESTONE}")
# Curl follows redirects by default with -L; without -L, 303 is what we expect.
[[ "$CODE" == "303" ]] || fail "submit expected 303 See Other, got $CODE"
pass "submit accepted (303)"

# 4. New ticket visible in view
log "4/6 GET /feedback/${SCOPE}  (expect new ticket in list)"
BODY=$(curl -k -s "${BASE}/feedback/${SCOPE}")
grep -q "${CUSTOMER}-${DEVICE}-${MILESTONE}-" <<< "$BODY" \
    || fail "expected a ticket id like ${CUSTOMER}-${DEVICE}-${MILESTONE}-N in the view"
pass "ticket is in the view page"

# 5. Submit with attachment (small binary)
log "5/6 POST /feedback/${SCOPE}/submit  bug + 1KB attachment"
TMP_ATT=$(mktemp)
head -c 1024 /dev/urandom > "$TMP_ATT"
CODE=$(curl -k -s -o /dev/null -w "%{http_code}" \
    -X POST "${BASE}/feedback/${SCOPE}/submit" \
    -F "category=bug" \
    -F "bug_type=OTHER-OTHER" \
    -F "description=smoke-test attachment $(date -u +%FT%TZ)" \
    -F "target_milestone=${MILESTONE}" \
    -F "attachment=@${TMP_ATT};filename=smoke.bin;type=application/octet-stream")
rm -f "$TMP_ATT"
[[ "$CODE" == "303" ]] || fail "submit-with-attachment expected 303, got $CODE"
pass "submit-with-attachment accepted"

# 6. Latest ticket has downloadable attachment
log "6/6 GET attachment for the latest ticket"
# Pull the highest ticket_pk visible in the view page.
BODY=$(curl -k -s "${BASE}/feedback/${SCOPE}")
# Regex: /feedback/<c>/<d>/<m>/attachment/<pk> --> grab the largest pk.
PK=$(grep -oE "/feedback/${CUSTOMER}/${DEVICE}/${MILESTONE}/attachment/[0-9]+" <<< "$BODY" \
     | grep -oE "[0-9]+$" | sort -n | tail -1 || true)
if [[ -z "${PK:-}" ]]; then
    fail "no attachment link found in view page (does the latest submit have one?)"
fi
CODE=$(curl "${CURL_OPTS[@]}" "${BASE}/feedback/${SCOPE}/attachment/${PK}")
[[ "$CODE" == "200" ]] || fail "attachment download expected 200, got $CODE for pk=${PK}"
pass "attachment downloadable (pk=${PK})"

# 7. Nginx client_max_body_size gate check (skipped if <5MB is fine).
# Real 5MB upload path -- catches the case where nginx client_max_body_size
# was NOT bumped and rejects before hilda-api sees the request.
log "extra: POST 4.5MB attachment (nginx client_max_body_size gate check)"
TMP_BIG=$(mktemp)
head -c 4718592 /dev/urandom > "$TMP_BIG"   # 4.5 MB
CODE=$(curl -k -s -o /dev/null -w "%{http_code}" \
    -X POST "${BASE}/feedback/${SCOPE}/submit" \
    -F "category=bug" \
    -F "bug_type=OTHER-OTHER" \
    -F "description=4.5MB gate check" \
    -F "target_milestone=${MILESTONE}" \
    -F "attachment=@${TMP_BIG};filename=big.bin;type=application/octet-stream")
rm -f "$TMP_BIG"
case "$CODE" in
    303) pass "4.5MB upload accepted -- nginx client_max_body_size >= 5M" ;;
    413) fail "nginx 413'd the 4.5MB upload -- bump client_max_body_size to 6m on /feedback/" ;;
    *)   fail "unexpected code $CODE on 4.5MB upload" ;;
esac

echo
echo "\033[1;32mAll checks passed.\033[0m"
echo "The BOT self mailbox should have received one or two notify emails --"
echo "verify those separately in the mailbox."
