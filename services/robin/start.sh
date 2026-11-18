#!/bin/bash
# R-206e: Tor in the background (Robin searches .onion engines through its
# SOCKS port 9050), then the wrapper. /health reports whether Tor bootstrapped.
: > "$ROBIN_TOR_LOG"
tor --Log "notice file $ROBIN_TOR_LOG" &
exec uvicorn app:app --app-dir /app --host 0.0.0.0 --port "${PORT:-10000}"
