# ────────────────────────────────────────────────────────────────────────────
# Dockerfile — LSMR + Bayesian Polymarket bot
#
# Pure Python; no LLM CLIs needed.  NordVPN is the separate gluetun sidecar.
# ────────────────────────────────────────────────────────────────────────────

FROM python:3.11-slim

RUN apt-get update \
   && apt-get install -y --no-install-recommends curl ca-certificates \
   && apt-get clean \
   && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . /app

COPY docker-entrypoint.sh /app/docker-entrypoint.sh
RUN chmod +x /app/docker-entrypoint.sh

ENTRYPOINT ["/app/docker-entrypoint.sh"]
