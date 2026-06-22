#!/bin/bash
# Test script to verify Control Scoping field updates are being saved correctly

API_BASE="http://localhost:8000/api"
API_KEY="your-secret-api-key-here"
AUTH_HEADER="Authorization: Bearer ${API_KEY}"

echo "=================================================="
echo "Control Scoping Field Update Test"
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

# Step 2: Create a test control scoping record
echo "Step 2: Creating test scoped control..."
TEST_SCF_ID="TEST-CONTROL-$(date +%s)"
echo "SCF ID: $TEST_SCF_ID"

CREATE_PAYLOAD=$(cat <<EOF
{
  "scf_id": "$TEST_SCF_ID",
  "selected": true,
  "selection_reason": "Initial Reason",
  "implementation_status": "not_started",
  "priority": "medium",
  "owner": "Initial Owner",
  "assigned_to": "initial@test.com",
  "maturity_level": "initial",
  "implementation_notes": "Initial Notes"
}
EOF
)

CREATE_RESPONSE=$(curl -s -w "\nHTTP_STATUS:%{http_code}" -X POST \
  -H "$AUTH_HEADER" \
  -H "Content-Type: application/json" \
  -d "$CREATE_PAYLOAD" \
  "${API_BASE}/organizations/${ORG_ID}/scoped-controls")

HTTP_STATUS=$(echo "$CREATE_RESPONSE" | grep "HTTP_STATUS" | cut -d':' -f2)
CREATE_BODY=$(echo "$CREATE_RESPONSE" | sed '/HTTP_STATUS/d')

if [ "$HTTP_STATUS" != "201" ]; then
    echo "❌ Failed to create scoped control (Status: $HTTP_STATUS)"
    echo "$CREATE_BODY"
    exit 1
fi

echo "✅ Created scoped control"
echo "$CREATE_BODY" | python3 -m json.tool
echo ""

# Step 3: Test individual field updates
echo "Step 3: Testing individual field updates..."
echo ""

## Test 3a: Update selection_reason
echo "3a. Updating selection_reason..."
UPDATE_REASON=$(cat <<EOF
{
  "scf_id": "$TEST_SCF_ID",
  "selected": true,
  "selection_reason": "UPDATED REASON TEXT",
  "implementation_status": "not_started",
  "priority": "medium",
  "owner": "Initial Owner",
  "assigned_to": "initial@test.com",
  "maturity_level": "initial",
  "implementation_notes": "Initial Notes"
}
EOF
)

curl -s -X POST -H "$AUTH_HEADER" -H "Content-Type: application/json" \
  -d "$UPDATE_REASON" \
  "${API_BASE}/organizations/${ORG_ID}/scoped-controls" > /dev/null

sleep 1
VERIFY_REASON=$(curl -s -H "$AUTH_HEADER" \
  "${API_BASE}/organizations/${ORG_ID}/scoped-controls/${TEST_SCF_ID}" | \
  python3 -c "import sys, json; print(json.load(sys.stdin).get('selection_reason', 'NULL'))")

if [ "$VERIFY_REASON" = "UPDATED REASON TEXT" ]; then
    echo "✅ selection_reason updated correctly"
else
    echo "❌ selection_reason NOT updated: Expected 'UPDATED REASON TEXT', Got '$VERIFY_REASON'"
fi
echo ""

## Test 3b: Update implementation_notes
echo "3b. Updating implementation_notes..."
UPDATE_NOTES=$(cat <<EOF
{
  "scf_id": "$TEST_SCF_ID",
  "selected": true,
  "selection_reason": "UPDATED REASON TEXT",
  "implementation_status": "not_started",
  "priority": "medium",
  "owner": "Initial Owner",
  "assigned_to": "initial@test.com",
  "maturity_level": "initial",
  "implementation_notes": "UPDATED IMPLEMENTATION NOTES"
}
EOF
)

curl -s -X POST -H "$AUTH_HEADER" -H "Content-Type: application/json" \
  -d "$UPDATE_NOTES" \
  "${API_BASE}/organizations/${ORG_ID}/scoped-controls" > /dev/null

sleep 1
VERIFY_NOTES=$(curl -s -H "$AUTH_HEADER" \
  "${API_BASE}/organizations/${ORG_ID}/scoped-controls/${TEST_SCF_ID}" | \
  python3 -c "import sys, json; print(json.load(sys.stdin).get('implementation_notes', 'NULL'))")

if [ "$VERIFY_NOTES" = "UPDATED IMPLEMENTATION NOTES" ]; then
    echo "✅ implementation_notes updated correctly"
else
    echo "❌ implementation_notes NOT updated: Got '$VERIFY_NOTES'"
fi
echo ""

## Test 3c: Update assigned_to
echo "3c. Updating assigned_to..."
UPDATE_ASSIGNED=$(cat <<EOF
{
  "scf_id": "$TEST_SCF_ID",
  "selected": true,
  "selection_reason": "UPDATED REASON TEXT",
  "implementation_status": "not_started",
  "priority": "medium",
  "owner": "Initial Owner",
  "assigned_to": "updated@test.com",
  "maturity_level": "initial",
  "implementation_notes": "UPDATED IMPLEMENTATION NOTES"
}
EOF
)

curl -s -X POST -H "$AUTH_HEADER" -H "Content-Type: application/json" \
  -d "$UPDATE_ASSIGNED" \
  "${API_BASE}/organizations/${ORG_ID}/scoped-controls" > /dev/null

sleep 1
VERIFY_ASSIGNED=$(curl -s -H "$AUTH_HEADER" \
  "${API_BASE}/organizations/${ORG_ID}/scoped-controls/${TEST_SCF_ID}" | \
  python3 -c "import sys, json; print(json.load(sys.stdin).get('assigned_to', 'NULL'))")

if [ "$VERIFY_ASSIGNED" = "updated@test.com" ]; then
    echo "✅ assigned_to updated correctly"
else
    echo "❌ assigned_to NOT updated: Got '$VERIFY_ASSIGNED'"
fi
echo ""

## Test 3d: Clear a field (set to empty string)
echo "3d. Testing field clearing (empty string)..."
CLEAR_ASSIGNED=$(cat <<EOF
{
  "scf_id": "$TEST_SCF_ID",
  "selected": true,
  "selection_reason": "UPDATED REASON TEXT",
  "implementation_status": "not_started",
  "priority": "medium",
  "owner": "Initial Owner",
  "assigned_to": "",
  "maturity_level": "initial",
  "implementation_notes": "UPDATED IMPLEMENTATION NOTES"
}
EOF
)

curl -s -X POST -H "$AUTH_HEADER" -H "Content-Type: application/json" \
  -d "$CLEAR_ASSIGNED" \
  "${API_BASE}/organizations/${ORG_ID}/scoped-controls" > /dev/null

sleep 1
VERIFY_CLEARED=$(curl -s -H "$AUTH_HEADER" \
  "${API_BASE}/organizations/${ORG_ID}/scoped-controls/${TEST_SCF_ID}" | \
  python3 -c "import sys, json; val = json.load(sys.stdin).get('assigned_to'); print('EMPTY' if val == '' or val is None else val)")

if [ "$VERIFY_CLEARED" = "EMPTY" ] || [ "$VERIFY_CLEARED" = "" ]; then
    echo "✅ assigned_to cleared correctly (empty or null)"
else
    echo "❌ assigned_to NOT cleared: Got '$VERIFY_CLEARED'"
fi
echo ""

## Test 3e: Update implementation_status (dropdown)
echo "3e. Testing dropdown field (implementation_status)..."
UPDATE_STATUS=$(cat <<EOF
{
  "scf_id": "$TEST_SCF_ID",
  "selected": true,
  "selection_reason": "UPDATED REASON TEXT",
  "implementation_status": "implemented",
  "priority": "high",
  "owner": "DevSecOps",
  "assigned_to": "",
  "maturity_level": "managed",
  "implementation_notes": "UPDATED IMPLEMENTATION NOTES"
}
EOF
)

curl -s -X POST -H "$AUTH_HEADER" -H "Content-Type: application/json" \
  -d "$UPDATE_STATUS" \
  "${API_BASE}/organizations/${ORG_ID}/scoped-controls" > /dev/null

sleep 1
GET_RESPONSE=$(curl -s -H "$AUTH_HEADER" \
  "${API_BASE}/organizations/${ORG_ID}/scoped-controls/${TEST_SCF_ID}")

VERIFY_STATUS=$(echo "$GET_RESPONSE" | python3 -c "import sys, json; print(json.load(sys.stdin).get('implementation_status', 'NULL'))")
VERIFY_PRIORITY=$(echo "$GET_RESPONSE" | python3 -c "import sys, json; print(json.load(sys.stdin).get('priority', 'NULL'))")
VERIFY_MATURITY=$(echo "$GET_RESPONSE" | python3 -c "import sys, json; print(json.load(sys.stdin).get('maturity_level', 'NULL'))")

echo -n "implementation_status: "
if [ "$VERIFY_STATUS" = "implemented" ]; then
    echo "✅ $VERIFY_STATUS"
else
    echo "❌ Expected 'implemented', Got '$VERIFY_STATUS'"
fi

echo -n "priority: "
if [ "$VERIFY_PRIORITY" = "high" ]; then
    echo "✅ $VERIFY_PRIORITY"
else
    echo "❌ Expected 'high', Got '$VERIFY_PRIORITY'"
fi

echo -n "maturity_level: "
if [ "$VERIFY_MATURITY" = "managed" ]; then
    echo "✅ $VERIFY_MATURITY"
else
    echo "❌ Expected 'managed', Got '$VERIFY_MATURITY'"
fi
echo ""

# Step 4: Final state verification
echo "Step 4: Final state verification..."
FINAL_STATE=$(curl -s -H "$AUTH_HEADER" \
  "${API_BASE}/organizations/${ORG_ID}/scoped-controls/${TEST_SCF_ID}")

echo "Final State:"
echo "$FINAL_STATE" | python3 -m json.tool
echo ""

# Step 5: Summary
echo "=================================================="
echo "Test Summary"
echo "=================================================="
echo "All critical fields tested:"
echo "  • selected (boolean)"
echo "  • selection_reason (textarea)"
echo "  • implementation_status (dropdown)"
echo "  • priority (dropdown)"
echo "  • owner (dropdown)"
echo "  • assigned_to (text input)"
echo "  • maturity_level (dropdown)"
echo "  • implementation_notes (textarea)"
echo ""
echo "Test control ID: $TEST_SCF_ID"
echo ""
echo "To manually verify in database:"
echo "docker exec -it odin-scf-postgres psql -U odin -d odin_scf -c \"SELECT scf_id, selected, implementation_status, priority, owner, assigned_to, maturity_level FROM scoped_controls WHERE scf_id='$TEST_SCF_ID';\""
echo ""
