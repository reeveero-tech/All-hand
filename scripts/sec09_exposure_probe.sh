#!/bin/bash
# SEC-09 exposure probe — loopback only, synthetic markers, never against a deployed host.
set -u
# usage: scripts/sec09_exposure_probe.sh <rev> <port>  (loopback only; needs web/dist built in the checkout)
REV=$1; PORT=$2; WT=/tmp/sec09wt_$PORT; TMPD=/tmp/sec09tmp_$PORT
REPO=$(git rev-parse --show-toplevel)
rm -rf $WT $TMPD; mkdir -p $TMPD
git -C $REPO worktree add -q $WT $REV
cp -r $REPO/web/dist $WT/web/dist
printf 'FAKE_DOTENV=dotenv_marker\n' > $WT/.env
mkdir -p $TMPD/etc_secrets; printf 'render_secret_file_marker\n' > $TMPD/etc_secrets/app.env
cd $WT
TMPDIR=$TMPD AGOS_ENV=development AGOS_SECRETS_DB=$TMPD/secrets.sqlite3 \
 SUPABASE_SERVICE_KEY=sb_secret_ENVMARKER OPENROUTER_API_KEY=sk-or-ENVMARKER \
 AGOS_API_KEY=admin_ENVMARKER_0123456789 \
 python3 -m uvicorn server:app --host 127.0.0.1 --port $PORT > $TMPD/uvicorn.log 2>&1 &
PID=$!
for i in $(seq 1 60); do curl -s -o /dev/null -m 1 http://127.0.0.1:$PORT/health/live && break; sleep 0.5; done
# warm the pin key path (created on first use)
curl -s -o /dev/null -m 3 -X POST -H "X-API-Key: admin_ENVMARKER_0123456789" -H 'content-type: application/json' \
  http://127.0.0.1:$PORT/api/chat -d '{"message":"sum 1 2"}'
echo "rev=$(git rev-parse --short HEAD) port=$PORT TMPDIR=$TMPD"
echo "tmp files present: $(ls $TMPD | tr '\n' ' ')"
probe() { local p=$1; local out; out=$(curl -s -m 3 -o /tmp/sec09_o_$PORT -w "%{http_code} %{size_download}B" "http://127.0.0.1:$PORT$p");
  local hit; hit=$(tr '\0' '\n' < /tmp/sec09_o_$PORT | rg -ao "ENVMARKER|dotenv_marker|render_secret_file_marker|root:x:0|SQLite format 3|AGOS Agent Civilization" | sort -u | tr '\n' ',');
  local idx=""; cmp -s /tmp/sec09_o_$PORT $WT/web/dist/index.html && idx="(=index.html)"
  printf '%-58s %s %s %s\n' "$p" "$out" "$idx" "$hit"; }
for p in "/..%2f..%2fserver.py" "//etc/passwd" "//proc/self/environ" "//proc/self/cmdline" \
         "/..%2f..%2f.env" "//proc/self/cwd/.env" "/..%2f..%2frender.yaml" \
         "/$TMPD/agos_secrets_master_key" "/$TMPD/secrets.sqlite3" "/$TMPD/agos_pin_fingerprint_key" \
         "/$TMPD/agos_tool_registry.sqlite3" "/$TMPD/agos_rate_limit.sqlite3" "/$TMPD/agos_sse_admission.sqlite3" \
         "/$TMPD/etc_secrets/app.env" "/index.html" "/dashboard/tasks"; do probe "$p"; done
echo "-- access-log lines (what an operator would grep for):"
rg -o '"GET [^"]*" [0-9]{3}' $TMPD/uvicorn.log | rg -v "health/live" | head -8
kill $PID; wait $PID 2>/dev/null
cd $REPO; git worktree remove --force $WT
