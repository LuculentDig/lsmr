#!/usr/bin/env bash
set -euo pipefail

# docker-entrypoint.sh
#
# VPN is handled by the gluetun sidecar container (see docker-compose.yml).
# This script waits until gluetun's health endpoint confirms the tunnel is up,
# then starts the bot.

MAX_WAIT=120  # seconds to wait for VPN to establish
INTERVAL=3

echo "[entrypoint] Waiting for VPN tunnel to be ready (up to ${MAX_WAIT}s)…"

# If the caller sets SKIP_VPN_CHECK=1 we assume the network namespace is
# already guarded by a VPN container that doesn’t expose the usual gluetun
# health endpoint (e.g. `musing_leakey`).  In that case just skip polling.
if [[ "${SKIP_VPN_CHECK:-0}" = "1" ]]; then
    echo "[entrypoint] SKIP_VPN_CHECK enabled; not probing health endpoint"
else
    elapsed=0
    while true; do
        # gluetun exposes a health endpoint on port 9999 (internal, same network namespace)
        health=$(curl -sf --max-time 5 http://localhost:9999/v1/openvpn/status 2>/dev/null || true)
        if echo "$health" | grep -q '"status":"running"'; then
            echo "[entrypoint] VPN tunnel confirmed — gluetun status=running"
            break
        fi
        # Fallback: also accept the NordVPN protected check
        ip_output=$(curl -sf --max-time 5 https://api.nordvpn.com/v1/helpers/ips/insights 2>/dev/null || true)
        if echo "$ip_output" | grep -q '"protected":true'; then
            echo "[entrypoint] VPN tunnel confirmed — protected=true"
            break
        fi
        if [[ $elapsed -ge $MAX_WAIT ]]; then
            echo "[entrypoint] WARNING: VPN not confirmed after ${MAX_WAIT}s — starting anyway."
            break
        fi
        echo "[entrypoint] VPN not ready yet (${elapsed}s elapsed), retrying…"
        sleep $INTERVAL
        elapsed=$((elapsed + INTERVAL))
    done
fi

# Start the main bot (foreground — container stays alive as long as bot runs)
echo "[entrypoint] Starting bot…"
exec python -u bot.py
