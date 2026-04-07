#!/bin/bash
# Start Rain Alert server

# Get script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Check if already running
if lsof -ti:58101 > /dev/null 2>&1; then
    echo "❌ Server is already running on port 58101"
    echo "   Use ./stop.sh to stop it first, or ./restart.sh to restart"
    exit 1
fi

# Activate virtual environment
if [ -d ".venv" ]; then
    echo "📦 Activating virtual environment..."
    source .venv/bin/activate
else
    echo "⚠️  Virtual environment not found (.venv)"
    echo "   Creating virtual environment..."
    python3 -m venv .venv
    source .venv/bin/activate
    echo "📦 Installing dependencies..."
    pip install -r requirements.txt
fi

# Start server in background
echo "🚀 Starting Rain Alert server..."
nohup env FLASK_PORT=58101 python run.py > rain-alert.log 2>&1 &
PID=$!

# Wait for server to start
sleep 4

# Check if server started successfully
if lsof -ti:58101 > /dev/null 2>&1; then
    echo "✅ Server started successfully (PID: $PID)"
    echo "   URL: http://127.0.0.1:58101"
    echo "   Log: tail -f rain-alert.log"
    echo ""
    echo "   To stop: ./stop.sh"
else
    echo "❌ Failed to start server"
    echo "   Check rain-alert.log for errors"
    exit 1
fi
