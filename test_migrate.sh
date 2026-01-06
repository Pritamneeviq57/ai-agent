#!/bin/bash

# Test script for /migrate-tables endpoint
# Usage: ./test_migrate.sh [YOUR_CRON_API_KEY]
# Note: API key is optional - if CRON_API_KEY is not set in Railway, you can omit it

API_KEY="${1:-$CRON_API_KEY}"
RAILWAY_URL="https://ai-agent-production-c956.up.railway.app"

echo "🔄 Testing /migrate-tables endpoint..."
echo "URL: $RAILWAY_URL/migrate-tables"
if [ -n "$API_KEY" ]; then
    echo "Using API key authentication"
    headers=(-H "X-API-Key: $API_KEY" -H "Content-Type: application/json")
else
    echo "No API key provided (endpoint will work if CRON_API_KEY is not set in Railway)"
    headers=(-H "Content-Type: application/json")
fi
echo ""

response=$(curl -s -w "\n%{http_code}" -X POST "$RAILWAY_URL/migrate-tables" \
  "${headers[@]}")

# Split response and status code
http_code=$(echo "$response" | tail -n1)
body=$(echo "$response" | sed '$d')

echo "HTTP Status: $http_code"
echo ""
echo "Response:"
echo "$body" | python3 -m json.tool 2>/dev/null || echo "$body"

if [ "$http_code" -eq 200 ]; then
    echo ""
    echo "✅ Success! Migration completed."
else
    echo ""
    echo "❌ Error: HTTP $http_code"
fi

