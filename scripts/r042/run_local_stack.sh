#!/usr/bin/env bash
# R-042: verify the storage layer against PostgreSQL + PostgREST (Supabase's engine).
#
# Requirements: a reachable PostgreSQL superuser connection (ADMIN_URI) and a
# PostgREST binary (POSTGREST_BIN). Used both locally and in CI (job `storage`).
#
#   ADMIN_URI      postgres superuser URI for the target database
#   AUTH_URI       URI PostgREST uses (role `authenticator`)
#   POSTGREST_BIN  path to postgrest
#   JWT_SECRET     throwaway test secret (>= 32 chars); NOT a real Supabase secret
#
# Exit code: that of the verification script (non-zero on any failed check),
# or non-zero if setup itself fails.
set -euo pipefail
cd "$(dirname "$0")/../.."

: "${ADMIN_URI:?}" "${AUTH_URI:?}" "${POSTGREST_BIN:?}"
JWT_SECRET="${JWT_SECRET:-r042-local-test-secret-not-a-real-key-000}"
PGRST_PORT="${PGRST_PORT:-3000}"
PROXY_PORT="${PROXY_PORT:-54321}"

echo "== apply schema (Supabase role emulation + docs/sql/001_init.sql + 002_tenant_isolation.sql)"
psql "$ADMIN_URI" -v ON_ERROR_STOP=1 -q -f scripts/r042/supabase_roles.sql
psql "$ADMIN_URI" -v ON_ERROR_STOP=1 -q -f docs/sql/001_init.sql
psql "$ADMIN_URI" -v ON_ERROR_STOP=1 -q -f docs/sql/002_tenant_isolation.sql  # SEC-02
psql "$ADMIN_URI" -v ON_ERROR_STOP=1 -q -f scripts/r042/grants_after_schema.sql
psql "$ADMIN_URI" -v ON_ERROR_STOP=1 -c "select table_name, column_name, data_type from information_schema.columns where table_schema='public' order by table_name, ordinal_position"

echo "== start PostgREST $("$POSTGREST_BIN" --version)"
PGRST_DB_URI="$AUTH_URI" PGRST_DB_SCHEMAS=public PGRST_DB_ANON_ROLE=anon \
PGRST_JWT_SECRET="$JWT_SECRET" PGRST_SERVER_PORT="$PGRST_PORT" PGRST_LOG_LEVEL=error \
  "$POSTGREST_BIN" > /tmp/r042_postgrest.log 2>&1 &
PGRST_PID=$!
python scripts/r042/kong_prefix_proxy.py "$PROXY_PORT" "http://127.0.0.1:$PGRST_PORT" > /tmp/r042_proxy.log 2>&1 &
PROXY_PID=$!
trap 'kill $PGRST_PID $PROXY_PID 2>/dev/null || true' EXIT

for _ in $(seq 1 30); do
  curl -fsS "http://127.0.0.1:$PROXY_PORT/rest/v1/" -o /dev/null 2>/dev/null && break
  sleep 0.5
done

export SUPABASE_URL="http://127.0.0.1:$PROXY_PORT"
# R-042b: the server uses the service_role key (RLS is on, no anon policies).
SUPABASE_SERVICE_KEY="$(python scripts/r042/make_jwt.py "$JWT_SECRET" service_role)"
ANON_JWT="$(python scripts/r042/make_jwt.py "$JWT_SECRET" anon)"
export SUPABASE_SERVICE_KEY
unset SUPABASE_ANON_KEY

echo "== negative control: request without a valid JWT must be rejected"
code=$(curl -s -o /dev/null -w '%{http_code}' -H "Authorization: Bearer invalid.token.x" "$SUPABASE_URL/rest/v1/tasks")
echo "invalid JWT -> HTTP $code"
[ "$code" = "401" ] || { echo "FAIL: invalid JWT not rejected"; exit 1; }

echo "== negative control: anon role must be denied by RLS (no policies)"
code=$(curl -s -o /tmp/r042_anon.txt -w '%{http_code}' -X POST -H "Authorization: Bearer $ANON_JWT" \
  -H "Content-Type: application/json" -d '{"task_id":"r042-anon-probe","specialization":"analyst"}' "$SUPABASE_URL/rest/v1/tasks")
echo "anon insert -> HTTP $code $(head -c 200 /tmp/r042_anon.txt)"
[ "$code" = "401" ] || [ "$code" = "403" ] || { echo "FAIL: anon write not denied"; exit 1; }

echo "== storage layer verification"
python scripts/run_r042_storage_verify.py

echo "== server end-to-end with live storage"
export R042_LIVE=1
python -m pytest tests/test_r042_live_storage.py -v -p no:cacheprovider

echo "== R-161 runner against the same local stack (local_integration)"
# loopback endpoint + the throwaway service key minted above: class
# local_integration (never live). Cleanup deletes only this run's tenant rows.
R161_STORAGE_URL="$SUPABASE_URL" R161_STORAGE_KEY="$SUPABASE_SERVICE_KEY" \
  python scripts/r161_live_integration.py --targets storage --report "${R161_REPORT:-/tmp/r161_storage_local.json}"
