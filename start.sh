#!/bin/bash
# Thin wrapper: Rain Alert runs as a LaunchAgent (see scripts/install-service.sh)
exec "$(dirname "$0")/scripts/install-service.sh" start
