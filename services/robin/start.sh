#!/bin/bash
# R-206e (D-165): start Tor, pick Robin's code, start the wrapper.
#
# Auto-update without any secret: on every start (Render free services also
# restart when they wake from sleep) the newest copy is fetched from OUR mirror
# (release mirror-robin, refreshed daily by tools-mirror.yml). It is used only
# if it unpacks and its pipeline imports with the installed packages; otherwise
# the copy built into the image (/app/robin) stays. If the upstream is archived
# or deleted, our mirror keeps the last copy, so nothing changes here.
: > "$ROBIN_TOR_LOG"
tor --Log "notice file $ROBIN_TOR_LOG" &

export ROBIN_CODE=/app/robin ROBIN_CODE_FROM=image
NEW=/home/robin/robin-latest
rm -rf "$NEW" /tmp/robin-latest.tar.gz && mkdir -p "$NEW"
if curl -fsSL --max-time 60 "$ROBIN_SRC_URL" -o /tmp/robin-latest.tar.gz &&
   tar xzf /tmp/robin-latest.tar.gz -C "$NEW" &&
   test -f "$NEW/robin/pipeline.py" &&
   (cd "$NEW/robin" && PYTHONPATH="$NEW/robin" timeout 60 python -c "import pipeline, search, llm_utils, config") >/dev/null 2>&1; then
  export ROBIN_CODE="$NEW/robin" ROBIN_CODE_FROM=mirror-at-start
  sha256sum /tmp/robin-latest.tar.gz | cut -d' ' -f1 > /home/robin/ROBIN_SRC_SHA256
else
  echo "robin: newest copy not usable; keeping the copy built into the image" >&2
  cp /app/ROBIN_SRC_SHA256 /home/robin/ROBIN_SRC_SHA256 2>/dev/null || true
fi
export PYTHONPATH="$ROBIN_CODE"
cd "$ROBIN_CODE"
exec uvicorn app:app --app-dir /app --host 0.0.0.0 --port "${PORT:-10000}"
