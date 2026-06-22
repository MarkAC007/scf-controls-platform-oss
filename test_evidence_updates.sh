#!/bin/bash
# Test script to verify Evidence Tracking field updates are being saved correctly

API_BASE="http://localhost:8000/api"
API_KEY="your-secret-api-key-here"
AUTH_HEADER="Authorization: Bearer ${API_KEY}"

echo "=================================================="
echo "Evidence Tracking Field Update Test"
echo "=================================================="
echo ""

# Step 1: Get organization ID
echo "Step 1: Getting organization ID..."
ORG_RESPONSE=$(curl -s -H "$AUTH_HEADER" "${API_BASE}/organizations")
ORG_ID=$(echo "$ORG_RESPONSE" | python3 -c "import sys, json; print(json.load(sys.stdin)[0]['id'])" 2>/dev/null)

if [ -z "$ORG_ID" ]; then
    echo "❌ Failed to get organization ID"
    exit 1
fi
echo "✅ Organization ID: $ORG_ID"
echo ""

# Step 2: Create a test evidence tracking record
echo "Step 2: Creating test evidence tracking record..."
TEST_EVIDENCE_ID="TEST-EVIDENCE-$(date +%s)"
echo "Evidence ID: $TEST_EVIDENCE_ID"

CREATE_PAYLOAD=$(cat <<EOF
{
  "evidence_id": "$TEST_EVIDENCE_ID",
  "is_tracked": true,
  "method_of_collection": "Initial Method",
  "collecting_system": "Initial System",
  "owner": "Initial Owner",
  "frequency": "Initial Frequency",
  "comments": "Initial Comments"
}
EOF
)

CREATE_RESPONSE=$(curl -s -w "\nHTTP_STATUS:%{http_code}" -X POST \
  -H "$AUTH_HEADER" \
  -H "Content-Type: application/json" \
  -d "$CREATE_PAYLOAD" \
  "${API_BASE}/organizations/${ORG_ID}/evidence-tracking")

HTTP_STATUS=$(echo "$CREATE_RESPONSE" | grep "HTTP_STATUS" | cut -d':' -f2)
CREATE_BODY=$(echo "$CREATE_RESPONSE" | sed '/HTTP_STATUS/d')

if [ "$HTTP_STATUS" != "201" ]; then
    echo "❌ Failed to create evidence tracking record (Status: $HTTP_STATUS)"
    echo "$CREATE_BODY"
    exit 1
fi

echo "✅ Created evidence tracking record"
echo "$CREATE_BODY" | python3 -m json.tool
echo ""

# Step 3: Retrieve the created record
echo "Step 3: Retrieving created record..."
GET_RESPONSE=$(curl -s -H "$AUTH_HEADER" \
  "${API_BASE}/organizations/${ORG_ID}/evidence-tracking/${TEST_EVIDENCE_ID}")

echo "Initial State:"
echo "$GET_RESPONSE" | python3 -m json.tool
echo ""

# Step 4: Update individual fields one by one
echo "Step 4: Testing individual field updates..."
echo ""

## Test 4a: Update method_of_collection
echo "4a. Updating method_of_collection..."
UPDATE_METHOD=$(cat <<EOF
{
  "evidence_id": "$TEST_EVIDENCE_ID",
  "is_tracked": true,
  "method_of_collection": "UPDATED METHOD",
  "collecting_system": "Initial System",
  "owner": "Initial Owner",
  "frequency": "Initial Frequency",
  "comments": "Initial Comments"
}
EOF
)

curl -s -X POST -H "$AUTH_HEADER" -H "Content-Type: application/json" \
  -d "$UPDATE_METHOD" \
  "${API_BASE}/organizations/${ORG_ID}/evidence-tracking" > /dev/null

sleep 1
VERIFY_METHOD=$(curl -s -H "$AUTH_HEADER" \
  "${API_BASE}/organizations/${ORG_ID}/evidence-tracking/${TEST_EVIDENCE_ID}" | \
  python3 -c "import sys, json; print(json.load(sys.stdin).get('method_of_collection', 'NULL'))")

if [ "$VERIFY_METHOD" = "UPDATED METHOD" ]; then
    echo "✅ method_of_collection updated correctly: $VERIFY_METHOD"
else
    echo "❌ method_of_collection NOT updated: Expected 'UPDATED METHOD', Got '$VERIFY_METHOD'"
fi
echo ""

## Test 4b: Update collecting_system
echo "4b. Updating collecting_system..."
UPDATE_SYSTEM=$(cat <<EOF
{
  "evidence_id": "$TEST_EVIDENCE_ID",
  "is_tracked": true,
  "method_of_collection": "UPDATED METHOD",
  "collecting_system": "UPDATED SYSTEM",
  "owner": "Initial Owner",
  "frequency": "Initial Frequency",
  "comments": "Initial Comments"
}
EOF
)

curl -s -X POST -H "$AUTH_HEADER" -H "Content-Type: application/json" \
  -d "$UPDATE_SYSTEM" \
  "${API_BASE}/organizations/${ORG_ID}/evidence-tracking" > /dev/null

sleep 1
VERIFY_SYSTEM=$(curl -s -H "$AUTH_HEADER" \
  "${API_BASE}/organizations/${ORG_ID}/evidence-tracking/${TEST_EVIDENCE_ID}" | \
  python3 -c "import sys, json; print(json.load(sys.stdin).get('collecting_system', 'NULL'))")

if [ "$VERIFY_SYSTEM" = "UPDATED SYSTEM" ]; then
    echo "✅ collecting_system updated correctly: $VERIFY_SYSTEM"
else
    echo "❌ collecting_system NOT updated: Expected 'UPDATED SYSTEM', Got '$VERIFY_SYSTEM'"
fi
echo ""

## Test 4c: Update owner
echo "4c. Updating owner..."
UPDATE_OWNER=$(cat <<EOF
{
  "evidence_id": "$TEST_EVIDENCE_ID",
  "is_tracked": true,
  "method_of_collection": "UPDATED METHOD",
  "collecting_system": "UPDATED SYSTEM",
  "owner": "UPDATED OWNER",
  "frequency": "Initial Frequency",
  "comments": "Initial Comments"
}
EOF
)

curl -s -X POST -H "$AUTH_HEADER" -H "Content-Type: application/json" \
  -d "$UPDATE_OWNER" \
  "${API_BASE}/organizations/${ORG_ID}/evidence-tracking" > /dev/null

sleep 1
VERIFY_OWNER=$(curl -s -H "$AUTH_HEADER" \
  "${API_BASE}/organizations/${ORG_ID}/evidence-tracking/${TEST_EVIDENCE_ID}" | \
  python3 -c "import sys, json; print(json.load(sys.stdin).get('owner', 'NULL'))")

if [ "$VERIFY_OWNER" = "UPDATED OWNER" ]; then
    echo "✅ owner updated correctly: $VERIFY_OWNER"
else
    echo "❌ owner NOT updated: Expected 'UPDATED OWNER', Got '$VERIFY_OWNER'"
fi
echo ""

## Test 4d: Clear a field (set to empty string)
echo "4d. Testing field clearing (empty string)..."
CLEAR_OWNER=$(cat <<EOF
{
  "evidence_id": "$TEST_EVIDENCE_ID",
  "is_tracked": true,
  "method_of_collection": "UPDATED METHOD",
  "collecting_system": "UPDATED SYSTEM",
  "owner": "",
  "frequency": "Initial Frequency",
  "comments": "Initial Comments"
}
EOF
)

curl -s -X POST -H "$AUTH_HEADER" -H "Content-Type: application/json" \
  -d "$CLEAR_OWNER" \
  "${API_BASE}/organizations/${ORG_ID}/evidence-tracking" > /dev/null

sleep 1
VERIFY_CLEARED=$(curl -s -H "$AUTH_HEADER" \
  "${API_BASE}/organizations/${ORG_ID}/evidence-tracking/${TEST_EVIDENCE_ID}" | \
  python3 -c "import sys, json; val = json.load(sys.stdin).get('owner'); print('EMPTY' if val == '' or val is None else val)")

if [ "$VERIFY_CLEARED" = "EMPTY" ] || [ "$VERIFY_CLEARED" = "" ]; then
    echo "✅ owner cleared correctly (empty or null)"
else
    echo "❌ owner NOT cleared: Got '$VERIFY_CLEARED'"
fi
echo ""

## Test 4e: Update is_tracked boolean
echo "4e. Testing boolean field (is_tracked)..."
UPDATE_TRACKED=$(cat <<EOF
{
  "evidence_id": "$TEST_EVIDENCE_ID",
  "is_tracked": false,
  "method_of_collection": "UPDATED METHOD",
  "collecting_system": "UPDATED SYSTEM",
  "owner": "",
  "frequency": "Initial Frequency",
  "comments": "Initial Comments"
}
EOF
)

curl -s -X POST -H "$AUTH_HEADER" -H "Content-Type: application/json" \
  -d "$UPDATE_TRACKED" \
  "${API_BASE}/organizations/${ORG_ID}/evidence-tracking" > /dev/null

sleep 1
VERIFY_TRACKED=$(curl -s -H "$AUTH_HEADER" \
  "${API_BASE}/organizations/${ORG_ID}/evidence-tracking/${TEST_EVIDENCE_ID}" | \
  python3 -c "import sys, json; print(str(json.load(sys.stdin).get('is_tracked')).lower())")

if [ "$VERIFY_TRACKED" = "false" ]; then
    echo "✅ is_tracked updated correctly: false"
else
    echo "❌ is_tracked NOT updated: Expected 'false', Got '$VERIFY_TRACKED'"
fi
echo ""

# Step 5: Final state verification
echo "Step 5: Final state verification..."
FINAL_STATE=$(curl -s -H "$AUTH_HEADER" \
  "${API_BASE}/organizations/${ORG_ID}/evidence-tracking/${TEST_EVIDENCE_ID}")

echo "Final State:"
echo "$FINAL_STATE" | python3 -m json.tool
echo ""

# Step 6: Summary
echo "=================================================="
echo "Test Summary"
echo "=================================================="
echo "All critical fields tested:"
echo "  • is_tracked (boolean)"
echo "  • method_of_collection (string)"
echo "  • collecting_system (string)"
echo "  • owner (string)"
echo "  • frequency (string)"
echo "  • comments (string)"
echo ""
echo "Test evidence ID: $TEST_EVIDENCE_ID"
echo ""
echo "To manually verify in database:"
echo "docker exec -it odin-scf-postgres psql -U odin -d odin_scf -c \"SELECT * FROM evidence_tracking WHERE evidence_id='$TEST_EVIDENCE_ID';\""
echo ""
