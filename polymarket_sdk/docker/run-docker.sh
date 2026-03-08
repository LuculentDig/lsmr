#!/usr/bin/env bash
# helper script to build/start/stop the compose stack based on the SDK
# templates above.  Copy it into a new project and adjust as necessary.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

CMD="${1:-start}"

case "$CMD" in
  stop)
    echo "Stopping containers…"
    docker compose down
    exit 0
    ;;
  logs)
    docker compose logs -f bot
    exit 0
    ;;
  status)
    docker compose ps
    exit 0
    ;;
  start|*)
    ;;
esac

if ! docker info >/dev/null 2>&1; then
    echo "Docker is not running. Opening Docker Desktop…"
    open -a "Docker"
    echo "Waiting for Docker to start…"
    for i in $(seq 1 30); do
        if docker info >/dev/null 2>&1; then
            break
        fi
        sleep 2
    done
    if ! docker info >/dev/null 2>&1; then
        echo "ERROR: Docker did not start in time. Please launch Docker Desktop manually."
        exit 1
    fi
fi

echo "Configuring macOS power settings to prevent sleep (lid-close safe)…"
sudo pmset -a sleep 0 disablesleep 1 standby 0 hibernatemode 0 2>/dev/null && \
    echo "  ✓ Sleep disabled (pmset)" || \
    echo "  WARNING: Could not configure pmset (not running as root?). Lid-close may still sleep."

# start containers

echo "Building and starting containers…"
docker compose up --build -d

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Bot is running inside Docker + NordVPN"
echo "  Dashboard → http://localhost:5050"
echo "  Logs      → ./run-docker.sh logs"
echo "  Stop      → ./run-docker.sh stop"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
