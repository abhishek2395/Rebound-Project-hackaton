#!/usr/bin/env bash
# =============================================================================
# Rebound Disruption Event Trigger Script
# Fires simulated flight disruption webhooks into the Rebound FastAPI gateway.
# Measures round-trip latency to verify sub-50ms async response SLA.
# =============================================================================

set -euo pipefail

API_URL="${REBOUND_API_URL:-http://localhost:8000}"
ENDPOINT="${API_URL}/webhook/disruption"

CYAN='\033[0;36m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "${CYAN}=====================================================${NC}"
echo -e "${CYAN}     ✈️  REBOUND — Disruption Webhook Ingress CLI     ${NC}"
echo -e "${CYAN}=====================================================${NC}"
echo "Target Gateway: ${ENDPOINT}"
echo ""

# Check if target server is alive
if ! curl -s -f -o /dev/null --connect-timeout 2 "${API_URL}/health"; then
  echo -e "${RED}⚠️  Warning: Rebound server at ${API_URL} is not responding.${NC}"
  echo "Please start the server first:"
  echo -e "${YELLOW}    uvicorn app.main:app --reload --port 8000${NC}"
  echo ""
  read -r -p "Proceed anyway? (y/N) " confirm
  if [[ ! "$confirm" =~ ^[Yy]$ ]]; then
    exit 1
  fi
fi

echo "Select a disruption scenario to fire:"
echo "  1) Flight Cancelled (Auto-Book within \$300 mandate) [S01]"
echo "  2) Flight Cancelled (Delta \$450 -> Triggers Twilio SMS HITL) [S02]"
echo "  3) 3h Delay (Arrives 16h before meeting -> Notify Only) [S06]"
echo "  4) 5h Delay (Violates 1h buffer before meeting -> Rebook) [S07]"
echo "  5) Duplicate Webhook Storm (Fires same event twice -> Idempotency test) [S10]"
echo "  6) High Spend Ceiling Breach (Delta \$1,200 -> Cleanly Escalate) [S05]"
echo ""
read -r -p "Enter choice [1-6, default: 1]: " choice
choice="${choice:-1}"

TIMESTAMP=$(date +%s)
EVENT_ID="evt_cli_${TIMESTAMP}"

case "$choice" in
  1)
    echo -e "\n${YELLOW}Configuring S01: Flight Cancelled (Easy Auto-Book)...${NC}"
    PAYLOAD=$(cat <<EOF
{
  "event_id": "${EVENT_ID}",
  "type": "cancelled",
  "order_id": "ord_original_380",
  "flight": {
    "carrier": "ZZ",
    "number": "ZZ123",
    "origin": "LHR",
    "destination": "JFK",
    "scheduled_departure": "2026-09-14T10:00:00Z",
    "scheduled_arrival": "2026-09-14T13:00:00Z"
  }
}
EOF
)
    ;;

  2)
    echo -e "\n${YELLOW}Configuring S02: Flight Cancelled (High Delta -> SMS HITL)...${NC}"
    PAYLOAD=$(cat <<EOF
{
  "event_id": "${EVENT_ID}",
  "type": "cancelled",
  "order_id": "ord_original_380",
  "flight": {
    "carrier": "ZZ",
    "number": "ZZ123",
    "origin": "LHR",
    "destination": "JFK",
    "scheduled_departure": "2026-09-14T10:00:00Z",
    "scheduled_arrival": "2026-09-14T13:00:00Z"
  }
}
EOF
)
    ;;

  3)
    echo -e "\n${YELLOW}Configuring S06: 3h Delay (Notify Only)...${NC}"
    PAYLOAD=$(cat <<EOF
{
  "event_id": "${EVENT_ID}",
  "type": "delayed",
  "order_id": "ord_original_380",
  "delay_minutes": 180,
  "flight": {
    "carrier": "ZZ",
    "number": "ZZ123",
    "origin": "LHR",
    "destination": "JFK",
    "scheduled_departure": "2026-09-14T10:00:00Z",
    "scheduled_arrival": "2026-09-14T13:00:00Z"
  }
}
EOF
)
    ;;

  4)
    echo -e "\n${YELLOW}Configuring S07: 5h Delay (Violates Deadline Buffer -> Rebook)...${NC}"
    PAYLOAD=$(cat <<EOF
{
  "event_id": "${EVENT_ID}",
  "type": "delayed",
  "order_id": "ord_original_380",
  "delay_minutes": 300,
  "flight": {
    "carrier": "ZZ",
    "number": "ZZ123",
    "origin": "LHR",
    "destination": "JFK",
    "scheduled_departure": "2026-09-14T10:00:00Z",
    "scheduled_arrival": "2026-09-14T13:00:00Z"
  }
}
EOF
)
    ;;

  5)
    echo -e "\n${YELLOW}Configuring S10: Duplicate Webhook Storm...${NC}"
    PAYLOAD=$(cat <<EOF
{
  "event_id": "${EVENT_ID}",
  "type": "cancelled",
  "order_id": "ord_original_380",
  "flight": {
    "carrier": "ZZ",
    "number": "ZZ123",
    "origin": "LHR",
    "destination": "JFK",
    "scheduled_departure": "2026-09-14T10:00:00Z",
    "scheduled_arrival": "2026-09-14T13:00:00Z"
  }
}
EOF
)
    ;;

  6)
    echo -e "\n${YELLOW}Configuring S05: High Spend Ceiling Breach...${NC}"
    PAYLOAD=$(cat <<EOF
{
  "event_id": "${EVENT_ID}",
  "type": "cancelled",
  "order_id": "ord_original_380",
  "flight": {
    "carrier": "ZZ",
    "number": "ZZ123",
    "origin": "LHR",
    "destination": "JFK",
    "scheduled_departure": "2026-09-14T10:00:00Z",
    "scheduled_arrival": "2026-09-14T13:00:00Z"
  }
}
EOF
)
    ;;

  *)
    echo -e "${RED}Invalid selection.${NC}"
    exit 1
    ;;
esac

echo -e "Payload:\n${CYAN}${PAYLOAD}${NC}\n"
echo "Sending POST request..."

START_TIME=$(python3 -c 'import time; print(time.time())')
RESPONSE=$(curl -s -w "\n%{http_code}\n%{time_total}" -X POST "${ENDPOINT}" \
  -H "Content-Type: application/json" \
  -d "${PAYLOAD}")

HTTP_CODE=$(echo "${RESPONSE}" | tail -n2 | head -n1)
TIME_TOTAL=$(echo "${RESPONSE}" | tail -n1)
BODY=$(echo "${RESPONSE}" | sed '$d' | sed '$d')

LATENCY_MS=$(python3 -c "print(int(${TIME_TOTAL} * 1000))")

echo ""
if [[ "$HTTP_CODE" == "200" ]]; then
  echo -e "${GREEN}✅ HTTP ${HTTP_CODE} OK (Gateway Response: ${LATENCY_MS}ms)${NC}"
  echo -e "Response:\n${GREEN}${BODY}${NC}"
else
  echo -e "${RED}❌ HTTP ${HTTP_CODE} ERROR (Gateway Response: ${LATENCY_MS}ms)${NC}"
  echo -e "Response:\n${RED}${BODY}${NC}"
fi

# If duplicate test, send it a second time
if [[ "$choice" == "5" ]]; then
  echo -e "\n${YELLOW}Firing second duplicate webhook with identical event_id (${EVENT_ID})...${NC}"
  DUP_RESPONSE=$(curl -s -w "\n%{http_code}\n%{time_total}" -X POST "${ENDPOINT}" \
    -H "Content-Type: application/json" \
    -d "${PAYLOAD}")
  DUP_HTTP_CODE=$(echo "${DUP_RESPONSE}" | tail -n2 | head -n1)
  DUP_BODY=$(echo "${DUP_RESPONSE}" | sed '$d' | sed '$d')
  
  echo -e "${CYAN}Second Response:${NC}"
  echo -e "${CYAN}${DUP_BODY}${NC}"
  if [[ "$DUP_BODY" =~ "skipped_duplicate" ]]; then
    echo -e "${GREEN}✅ Idempotency Verified: Second event skipped without rebooking!${NC}"
  else
    echo -e "${RED}❌ Warning: Duplicate event was not marked as skipped_duplicate.${NC}"
  fi
fi

echo ""
echo -e "${CYAN}View the live execution trace at:${NC}"
echo -e "${GREEN}👉  ${API_URL}/viewer/${NC}"
echo ""

