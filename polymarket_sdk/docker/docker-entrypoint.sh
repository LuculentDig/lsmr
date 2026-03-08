#!/usr/bin/env bash
set -euo pipefail

# example entrypoint for a VPN-protected Polymarket bot.  waits for the
# nordvpn sidecar (gluetun) to report that the tunnel is up before launching
# ``python -u bot.py`` in the same network namespace.

MAX_WAIT=120  # seconds to wait for VPN to establish
INTERVAL=3

echo "[entrypoint] Waiting for VPN tunnel to be ready (up to ${MAX_WAIT}s)…"

if [[ "${SKIP_VPN_CHECK:-0}" = "1" ]]; then
    echo "[entrypoint] SKIP_VPN_CHECK enabled; not probing health endpoint"
else
    elapsed=0
    while true; do
        health=$(curl -sf --max-time 5 http://localhost:9999/v1/openvpn/status 2>/dev/null || true)
        if echo "$health" | grep -q '"status":"running"'; then
            echo "[entrypoint] VPN tunnel confirmed — gluetun status=running"
            break
        fi
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

# start the bot in the foreground
exec python -u bot.py
