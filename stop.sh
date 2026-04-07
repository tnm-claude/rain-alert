#!/bin/bash
# Stop Rain Alert server

echo "🛑 Stopping Rain Alert server..."

# Find processes on port 58101
PIDS=$(lsof -ti:58101)

if [ -z "$PIDS" ]; then
    echo "ℹ️  No server running on port 58101"
    exit 0
fi

# Kill processes
echo "$PIDS" | xargs kill -9 2>/dev/null

# Wait a moment
sleep 1

# Verify stopped
if lsof -ti:58101 > /dev/null 2>&1; then
    echo "❌ Failed to stop server"
    exit 1
else
    echo "✅ Server stopped successfully"
fi
