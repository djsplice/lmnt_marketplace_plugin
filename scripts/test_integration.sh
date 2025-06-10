#!/bin/bash
# LMNT Marketplace Plugin Integration Test Script
# This script tests all the endpoints and functionality of the LMNT Marketplace Plugin

echo "LMNT Marketplace Plugin Integration Test"
echo "========================================"
echo "Testing on: $(date)"

# Base URL for the Moonraker API
BASE_URL="http://localhost:7125"

# Test status endpoint
echo -e "\n1. Testing status endpoint..."
STATUS_RESPONSE=$(curl -s $BASE_URL/machine/lmnt_marketplace/status)
echo "Status Response:"
echo "$STATUS_RESPONSE" | python3 -m json.tool

# Test user login
echo -e "\n2. Testing user login..."
echo "Enter test username (or press Enter to use 'test_user'):"
read TEST_USERNAME
TEST_USERNAME=${TEST_USERNAME:-test_user}

echo "Enter test password (or press Enter to use 'test_password'):"
read TEST_PASSWORD
TEST_PASSWORD=${TEST_PASSWORD:-test_password}

LOGIN_RESPONSE=$(curl -s -X POST $BASE_URL/machine/lmnt_marketplace/user_login \
  -H "Content-Type: application/json" \
  -d "{\"username\":\"$TEST_USERNAME\", \"password\":\"$TEST_PASSWORD\"}")

echo "Login Response:"
echo "$LOGIN_RESPONSE" | python3 -m json.tool

# Extract token from response (if successful)
USER_TOKEN=$(echo $LOGIN_RESPONSE | python3 -c "import sys, json; data = json.load(sys.stdin); print(data.get('result', {}).get('token', ''))" 2>/dev/null)

if [ -n "$USER_TOKEN" ] && [ "$USER_TOKEN" != "null" ]; then
  echo -e "\nLogin successful! Token received."
  
  # Test printer registration
  echo -e "\n3. Testing printer registration..."
  echo "Enter printer name (or press Enter to use 'Test Printer'):"
  read PRINTER_NAME
  PRINTER_NAME=${PRINTER_NAME:-Test Printer}
  
  REGISTER_RESPONSE=$(curl -s -X POST $BASE_URL/machine/lmnt_marketplace/register_printer \
    -H "Content-Type: application/json" \
    -d "{\"user_token\":\"$USER_TOKEN\", \"printer_name\":\"$PRINTER_NAME\"}")
  
  echo "Registration Response:"
  echo "$REGISTER_RESPONSE" | python3 -m json.tool
  
  # Test job checking
  echo -e "\n4. Testing job check..."
  JOB_CHECK_RESPONSE=$(curl -s -X POST $BASE_URL/machine/lmnt_marketplace/check_jobs)
  
  echo "Job Check Response:"
  echo "$JOB_CHECK_RESPONSE" | python3 -m json.tool
  
  # Test status again to see if authentication status changed
  echo -e "\n5. Testing status endpoint after authentication..."
  STATUS_RESPONSE=$(curl -s $BASE_URL/machine/lmnt_marketplace/status)
  echo "Status Response:"
  echo "$STATUS_RESPONSE" | python3 -m json.tool
else
  echo -e "\nLogin failed or token not received. Skipping registration and job tests."
fi

echo -e "\nTest completed at $(date)"
