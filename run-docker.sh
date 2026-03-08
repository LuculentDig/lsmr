#!/usr/bin/env bash
# run-docker.sh — build and start the two-container Polymarket bot
#
# Usage:
#   ./run-docker.sh            start (or restart) everything
#   ./run-docker.sh stop       stop all containers
#   ./run-docker.sh logs       follow bot logs
#   ./run-docker.sh status     show container status
#
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

# ── Ensure Docker Desktop is running ──────────────────────────────────────
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

# ── Disable macOS sleep so the bot keeps running with lid closed ───────────
# This is the key fix: inhibit ALL sleep (including lid-close) at the system level.
# Without battery flag (-a), macOS still sleeps when on battery; we prevent that here.
echo "Configuring macOS power settings to prevent sleep (lid-close safe)…"
sudo pmset -a sleep 0 disablesleep 1 standby 0 hibernatemode 0 2>/dev/null && \
    echo "  ✓ Sleep disabled (pmset)" || \
    echo "  WARNING: Could not configure pmset (not running as root?). Lid-close may still sleep."

# ── Start containers ───────────────────────────────────────────────────────
echo "Building and starting containers…"
docker compose up --build -d

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Bot is running inside Docker + NordVPN"
echo "  Dashboard → http://localhost:5050"
echo "  Logs      → ./run-docker.sh logs"
echo "  Stop      → ./run-docker.sh stop"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
