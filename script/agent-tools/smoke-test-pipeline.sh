#!/bin/bash
# Smoke test: submit a URL to staging and verify the full processing pipeline completes.
# This is the ONE test that would have caught all three runtime bugs.
# Usage: ./script/agent-tools/smoke-test-pipeline.sh [base_url]

set -euo pipefail

BASE_URL="${1:-https://tasche-staging.adewale-883.workers.dev}"
TEST_URL="https://example.com/smoke-test-$(date +%s)"
MAX_WAIT=120  # seconds

echo "=== Pipeline Smoke Test ==="
echo "Target: $BASE_URL"
echo "Test URL: $TEST_URL"
echo ""

# Step 1: Submit article
echo "[1/4] Submitting article..."
RESPONSE=$(curl -sf -X POST "$BASE_URL/api/articles" \
  -H "Content-Type: application/json" \
  -d "{\"url\": \"$TEST_URL\"}")
ARTICLE_ID=$(echo "$RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")
echo "  Created: $ARTICLE_ID"

# Step 2: Poll for processing
echo "[2/4] Waiting for queue processing (max ${MAX_WAIT}s)..."
ELAPSED=0
while [ $ELAPSED -lt $MAX_WAIT ]; do
  sleep 5
  ELAPSED=$((ELAPSED + 5))
  STATUS=$(curl -sf "$BASE_URL/api/articles/$ARTICLE_ID" | \
    python3 -c "import sys,json; print(json.load(sys.stdin)['status'])" 2>/dev/null || echo "error")
  echo "  ${ELAPSED}s: status=$STATUS"
  if [ "$STATUS" = "ready" ] || [ "$STATUS" = "failed" ]; then
    break
  fi
done

# Step 3: Verify result
echo "[3/4] Checking final state..."
ARTICLE=$(curl -sf "$BASE_URL/api/articles/$ARTICLE_ID")
STATUS=$(echo "$ARTICLE" | python3 -c "import sys,json; print(json.load(sys.stdin)['status'])")
TITLE=$(echo "$ARTICLE" | python3 -c "import sys,json; print(json.load(sys.stdin).get('title') or '(none)')")
HTML_KEY=$(echo "$ARTICLE" | python3 -c "import sys,json; print(json.load(sys.stdin).get('html_key') or '(none)')")

echo "  Status:   $STATUS"
echo "  Title:    $TITLE"
echo "  HTML Key: $HTML_KEY"

# Step 4: Cleanup
echo "[4/4] Cleaning up..."
curl -sf -X DELETE "$BASE_URL/api/articles/$ARTICLE_ID" > /dev/null 2>&1 || true

echo ""
if [ "$STATUS" = "ready" ]; then
  echo "PASS: Pipeline completed successfully"
  exit 0
elif [ "$STATUS" = "failed" ]; then
  echo "FAIL: Pipeline failed (article status=failed)"
  exit 1
else
  echo "FAIL: Pipeline timed out after ${MAX_WAIT}s (status=$STATUS)"
  exit 1
fi
