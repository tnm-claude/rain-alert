#!/bin/bash
# Restart Rain Alert server

# Get script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "🔄 Restarting Rain Alert server..."
echo ""

# Stop server
./stop.sh

echo ""

# Start server
./start.sh
