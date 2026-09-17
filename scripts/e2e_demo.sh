#!/usr/bin/env bash
# End-to-end demo: create assessment → upload fixture PDFs → evaluate → results.
# Prereqs: app on :8090, ES reachable, rules seeded, fixture PDFs generated.
set -euo pipefail

BASE="${BASE_URL:-http://localhost:8090}"
FIXTURES="$(cd "$(dirname "$0")/.." && pwd)/fixtures/policies"
RULES="${RULE_IDS:-}"   # optional comma-separated rule subset

echo "==> health"
curl -fsS "$BASE/health" && echo
curl -fsS "$BASE/ready" && echo

echo "==> create assessment"
ASSESSMENT_ID=$(curl -fsS -X POST "$BASE/api/v1/assessments" \
  -H 'Content-Type: application/json' \
  -d '{"name":"e2e-demo","framework":"SOC2"}' | python3 -c 'import json,sys;print(json.load(sys.stdin)["assessment_id"])')
echo "assessment_id=$ASSESSMENT_ID"

echo "==> upload documents"
curl -fsS -X POST "$BASE/api/v1/assessments/$ASSESSMENT_ID/documents" \
  -F "files=@$FIXTURES/access_control_policy.pdf" \
  -F "files=@$FIXTURES/incident_response_policy.pdf" \
  -F "files=@$FIXTURES/change_management_policy.pdf" \
  -F "files=@$FIXTURES/information_security_policy.pdf" \
  -F "files=@$FIXTURES/business_continuity_policy.pdf" \
  -F "files=@$FIXTURES/acceptable_use_policy.pdf" | python3 -m json.tool

echo "==> start evaluation"
if [ -n "$RULES" ]; then
  RULE_JSON=$(python3 -c "import json,sys;print(json.dumps({'rule_ids':sys.argv[1].split(',')}))" "$RULES")
else
  RULE_JSON='{}'
fi
curl -fsS -X POST "$BASE/api/v1/assessments/$ASSESSMENT_ID/evaluate" \
  -H 'Content-Type: application/json' -d "$RULE_JSON" && echo

echo "==> polling status"
for i in $(seq 1 120); do
  STATUS=$(curl -fsS "$BASE/api/v1/assessments/$ASSESSMENT_ID" | python3 -c 'import json,sys;print(json.load(sys.stdin)["status"])')
  echo "  [$i] $STATUS"
  case "$STATUS" in
    COMPLETED|PARTIAL|FAILED) break ;;
  esac
  sleep 10
done

echo "==> results"
curl -fsS "$BASE/api/v1/assessments/$ASSESSMENT_ID/results" | python3 -m json.tool
