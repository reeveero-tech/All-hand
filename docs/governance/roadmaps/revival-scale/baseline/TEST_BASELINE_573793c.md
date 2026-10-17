# خط أساس اختبارات الملفات — R-158 عند `573793c1f36afd77f3b79247ab9e2d978a397b62`

> مولَّد من [`TEST_BASELINE_573793c.json`](TEST_BASELINE_573793c.json) بـ`scripts/build_test_baseline.py` — لا يُحرر يدوياً. البصمة (الجزء الحتمي): `d2523d8ca38443c6e8fa84be37445f5dc5362accdd41caa4d292737969c33474`.

- الملفات المتعقبة: **1917** — كل ملف في مجموعة واحدة (fail=32, integrity-only=591, pass=1294).
- الفحص الذاتي: **PASS**
- السجل الخام لهذا التشغيل: `090_r158_baseline_run3_clean_clone.log` (أقسام `=== [<id>] ===`).

## البيئة

| الأداة | الإصدار |
|---|---|
| PyYAML | `6.0.3` |
| aiohttp | `3.14.5` |
| bash | `GNU bash, version 5.3.9(1)-release (x86_64-pc-linux-gnu)` |
| coverage | `7.16.2` |
| fastapi | `0.143.3` |
| git | `git version 2.53.0` |
| httpx | `not installed` |
| implementation | `CPython` |
| node | `v22.22.0` |
| npm | `10.9.4` |
| os | `Linux x86_64` |
| postgres | `postgres (PostgreSQL) 17.11` |
| postgrest | `PostgREST 12.2.3` |
| pydantic | `2.14.1` |
| pytest | `9.1.1` |
| pytest-asyncio | `1.4.0` |
| pytest-cov | `7.1.0` |
| python | `3.12.13` |
| ruff | `0.17.0` |
| starlette | `1.8.0` |
| uvicorn | `0.54.0` |

حزم Python المثبتة: 42 (القائمة الكاملة في JSON). متغيرات ثابتة: `CI=1`, `LANG=C.UTF-8`, `LC_ALL=C.UTF-8`, `PYTHONHASHSEED=0`, `TZ=UTC`.

## المجموعات

| المجموعة | الفئة | الملفات | حالة الملفات | النتيجة المحلية |
|---|---|---|---|---|
| `tests-python` — Python test suite (tests/) | unit/contract/integration/live | 53 | fail=1, pass=52 | **fail** |
| `py-specialists` — 1,000 specialist modules + templates | compile/import/contract | 1044 | pass=1044 | **pass** |
| `py-runtime` — Python runtime packages + server | compile/import/lint/unit/integration | 48 | pass=48 | **pass** |
| `sql-storage` — SQL schema + live storage stack | schema/integration/live | 7 | pass=7 | **pass** |
| `py-scripts` — CLI and probe scripts | compile/lint/smoke | 32 | pass=32 | **fail** |
| `shell` — Shell scripts | syntax | 1 | pass=1 | **pass** |
| `frontend-web` — web/ (deployed React UI) | build/unit/contract | 16 | pass=16 | **pass** |
| `frontend-root` — Root app src/ (Q-002: deployment undecided) | lint/build/type | 25 | fail=25 | **fail** |
| `deploy-ci` — Docker, Render, CI workflows | config/static/ci | 5 | pass=5 | **pass** |
| `deps-config` — Dependency locks and tool config | config | 9 | pass=9 | **pass** |
| `governance-data` — Governance templates, specs, inventories, maps | schema | 8 | pass=8 | **pass** |
| `evidence-scripts` — Historical audit scripts (docs/audit/*) | compile | 15 | fail=3, pass=12 | **pass** |
| `evidence-data` — Audit evidence data (json/jsonl/txt/csv) | integrity | 34 | fail=2, integrity-only=32 | **fail** |
| `evidence-logs` — Raw audit logs (docs/audit/**/*.log) | integrity | 553 | fail=1, integrity-only=552 | **fail** |
| `binary-assets` — Binary assets (screenshots) | integrity | 7 | integrity-only=7 | **pass** |
| `docs` — Markdown documentation | links | 60 | pass=60 | **pass** |

## الخطوات والأوامر

| المجموعة / الخطوة | نوع الدليل | الأمر | exit | pass | fail | skip | xfail | xpass | deselected | errors |
|---|---|---|---|---|---|---|---|---|---|---|
| `tests-python` / `compile` | local-test | `compile(source, path, 'exec') (53 files of this group)` | 0 | 53 | 0 | 0 | 0 | 0 | 0 | 0 |
| `tests-python` / `pytest:real` | local-test | `python -m pytest tests/ -q -rfE -p no:cacheprovider --junitxml=<tmp> -o junit_family=xunit1 --cov=. --cov-report=<tmp> --cov-report=` | 0 | 1056 | 0 | 4 | 0 | 0 | 4 | 0 |
| `tests-python` / `pytest:specialists` | local-test | `AGOS_AGENT_IMPL=specialists python -m pytest tests/ -q -rfE -p no:cacheprovider --junitxml=<tmp> -o junit_family=xunit1` | 1 | 1053 | 3 | 4 | 0 | 0 | 4 | 0 |
| `tests-python` / `pytest:network` | local-live-internet | `python -m pytest tests/ -m network -q -rfE` | 1 | 3 | 1 | 0 | 0 | 0 | 1060 | 0 |
| `tests-python` / `ci:test` | ci | `GitHub Actions run 39005652192 job test` | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `tests-python` / `ci:network` | ci | `GitHub Actions run 39005652192 job network` | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `py-specialists` / `compile` | local-test | `compile(source, path, 'exec') (1044 files of this group)` | 0 | 1044 | 0 | 0 | 0 | 0 | 0 | 0 |
| `py-specialists` / `import` | local-test | `importlib.import_module per module (AGOS_ENV=test, AGENTS_PER_SPEC=1)` | 0 | 1044 | 0 | 0 | 0 | 0 | 0 | 0 |
| `py-runtime` / `compile` | local-test | `compile(source, path, 'exec') (48 files of this group)` | 0 | 48 | 0 | 0 | 0 | 0 | 0 | 0 |
| `py-runtime` / `import` | local-test | `importlib.import_module per module (AGOS_ENV=test, AGENTS_PER_SPEC=1)` | 0 | 48 | 0 | 0 | 0 | 0 | 0 | 0 |
| `py-runtime` / `ruff:py-runtime` | local-test | `ruff check --select F,E722 --output-format json --no-cache server.py agent_civilization agos_kernel tests` | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `sql-storage` / `compile` | local-test | `compile(source, path, 'exec') (2 files of this group)` | 0 | 2 | 0 | 0 | 0 | 0 | 0 | 0 |
| `sql-storage` / `storage:stack` | local-operational | `bash scripts/r042/run_local_stack.sh (fresh PostgreSQL + PostgREST v12.2.3)` | 0 | 4 | 0 | 0 | 0 | 0 | 0 | 0 |
| `sql-storage` / `bash-n:scripts/r042/run_local_stack.sh` | local-test | `bash -n scripts/r042/run_local_stack.sh` | 0 | 1 | 0 | 0 | 0 | 0 | 0 | 0 |
| `sql-storage` / `ci:storage` | ci | `GitHub Actions run 39005652192 job storage` | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `py-scripts` / `compile` | local-test | `compile(source, path, 'exec') (32 files of this group)` | 0 | 32 | 0 | 0 | 0 | 0 | 0 | 0 |
| `py-scripts` / `ruff:py-scripts` | local-test | `ruff check --select F,E722 --output-format json --no-cache scripts run.py docs/audit` | 1 | 0 | 5 | 0 | 0 | 0 | 0 | 0 |
| `shell` / `bash-n:docs/audit/M3/logs/gate_test_harness.sh` | local-test | `bash -n docs/audit/M3/logs/gate_test_harness.sh` | 0 | 1 | 0 | 0 | 0 | 0 | 0 | 0 |
| `frontend-web` / `frontend-web:ci` | local-test | `(cd web) npm ci --engine-strict` | 0 | 1 | 0 | 0 | 0 | 0 | 0 | 0 |
| `frontend-web` / `frontend-web:build` | local-test | `(cd web) npm run build` | 0 | 1 | 0 | 0 | 0 | 0 | 0 | 0 |
| `frontend-web` / `frontend-web:test` | local-test | `(cd web) npm test` | 0 | 31 | 0 | 0 | 0 | 0 | 0 | 0 |
| `frontend-web` / `ci:build` | ci | `GitHub Actions run 39005652192 job build` | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `frontend-root` / `frontend-root:ci` | local-test | `npm ci --engine-strict` | 0 | 1 | 0 | 0 | 0 | 0 | 0 | 0 |
| `frontend-root` / `frontend-root:eslint` | local-test | `npx eslint . --max-warnings=0` | 0 | 1 | 0 | 0 | 0 | 0 | 0 | 0 |
| `frontend-root` / `frontend-root:build` | local-test | `npm run build` | 0 | 1 | 0 | 0 | 0 | 0 | 0 | 0 |
| `frontend-root` / `frontend-root:tsc` | local-test | `npx tsc --noEmit -p tsconfig.json` | 2 | 0 | 1 | 0 | 0 | 0 | 0 | 0 |
| `frontend-root` / `ci:build` | ci | `GitHub Actions run 39005652192 job build` | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `deploy-ci` / `deploy:map-self-check` | local-test | `python scripts/build_entrypoint_map.py <checkout> <sha> <tmp>` | 0 | 1 | 0 | 0 | 0 | 0 | 0 | 0 |
| `deploy-ci` / `ci:docker` | ci | `GitHub Actions run 39005652192 job docker` | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `deploy-ci` / `ci:quality` | ci | `GitHub Actions run 39005652192 job quality` | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `deps-config` / `ci:audit` | ci | `GitHub Actions run 39005652192 job audit` | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `deps-config` / `config:parse` | local-test | `lock hash-pinning / requirement syntax / ini / env` | 0 | 9 | 0 | 0 | 0 | 0 | 0 | 0 |
| `governance-data` / `data:parse` | local-test | `json.load / json lines / csv width / utf-8` | 0 | 8 | 0 | 0 | 0 | 0 | 0 | 0 |
| `governance-data` / `data:seed-vs-schema` | local-test | `required keys/types of docs/governance/roadmaps/revival-scale/specs/tool_registry_contract.schema.json on docs/governance/roadmaps/revival-scale/templates/tool_registry_seed.json` | 0 | 1 | 0 | 0 | 0 | 0 | 0 | 0 |
| `governance-data` / `gate:status-evidence` | local-test | `python scripts/check_status_evidence.py .` | 0 | 1 | 0 | 0 | 0 | 0 | 0 | 0 |
| `governance-data` / `gate:no-delete` | local-test | `python scripts/check_no_delete.py <sha>^1 <sha>` | 0 | 1 | 0 | 0 | 0 | 0 | 0 | 0 |
| `governance-data` / `gate:atomic-v2` | local-test | `python scripts/check_atomic_batch.py <sha>^1 <sha>` | 0 | 1 | 0 | 0 | 0 | 0 | 0 | 0 |
| `evidence-scripts` / `compile` | local-test | `compile(source, path, 'exec') (15 files of this group)` | 0 | 15 | 0 | 0 | 0 | 0 | 0 | 0 |
| `evidence-data` / `data:parse` | local-test | `json.load / json lines / csv width / utf-8` | 1 | 32 | 2 | 0 | 0 | 0 | 0 | 0 |
| `evidence-logs` / `integrity` | local-test | `UTF-8 decode` | 1 | 552 | 1 | 0 | 0 | 0 | 0 | 0 |
| `binary-assets` / `integrity` | local-test | `PNG signature` | 0 | 7 | 0 | 0 | 0 | 0 | 0 | 0 |
| `docs` / `docs:links` | local-test | `relative markdown links resolve to tracked paths` | 0 | 60 | 0 | 0 | 0 | 0 | 0 | 0 |

## سبب ملاءمة كل نوع تحقق

- `tests-python`: pytest is the project's test runner (pytest.ini); the same suite CI's job 'test' runs, executed twice: default runtime (real) and AGOS_AGENT_IMPL=specialists, because CI only runs 'real'.
- `py-specialists`: Generated thin shims + 20 templates: compile + import every module (each is importable by name) and exercise through the suite in specialists mode; per-file unit tests would test the generator twice.
- `py-runtime`: Live code: compile, import each module, ruff with CI's fatal rule set, and line coverage from the suite (which file the tests actually execute).
- `sql-storage`: SQL is only meaningful applied to the engine Supabase runs: a fresh PostgreSQL + PostgREST (scripts/r042/run_local_stack.sh, the script CI's job 'storage' runs) applies all 4 SQL files with ON_ERROR_STOP=1 and runs tests/test_r042_live_storage.py over HTTP.
- `py-scripts`: Compile + ruff (fatal set; not in CI's lint scope) + coverage from the suite (governance/inventory/map CLIs are exercised by tests/test_r152..r156). Probes needing a live server are not executed here.
- `shell`: bash -n syntax check; run_local_stack.sh is additionally executed by sql-storage.
- `frontend-web`: npm ci --engine-strict, vite build, node --test (API contract + UI consent tests) — CI job 'build'.
- `frontend-root`: npm ci, eslint --max-warnings=0, vite build (CI job 'build'), then tsc --noEmit (NOT in CI). Tested and built; its deployment is NOT decided (Q-002).
- `deploy-ci`: YAML parse + R-156 map self-check (ASGI targets, COPY sources, workflow script references exist). Docker image build/run and the workflows themselves are proven only by CI on the same SHA.
- `deps-config`: Locks must be fully hash-pinned (pip --require-hashes in CI); pytest.ini must parse; .env.example must be KEY=VALUE lines. The installed set is what this baseline ran on.
- `governance-data`: Parse every CSV/JSON; CSV rows must match the header width; the tool-registry seed is checked against the required keys/types of its schema; governance gates run on the snapshot.
- `evidence-scripts`: One-off evidence scripts of past batches: compile only — they target past live environments.
- `evidence-data`: Recorded evidence, not executable: JSON/JSONL must parse, text must be UTF-8.
- `evidence-logs`: Raw logs are evidence records (never edited, R-152): UTF-8 readability only; content is the evidence.
- `binary-assets`: Images cannot be tested; PNG signature check proves the blob is an intact PNG.
- `docs`: Relative links must resolve to a tracked path at the SHA (anchors/URLs not checked).

## الإخفاقات والفجوات المسجلة

- `tests-python` / `pytest:specialists` (local-test, exit 1): `tests/test_r149_security_qa_gaps.py::test_handler_exception_inside_real_agent_ends_task_failed`, `tests/test_r149_security_qa_gaps.py::test_internal_dependency_timeout_ends_failed_without_leak`, `tests/test_r149_security_qa_gaps.py::test_internal_exception_marker_reaches_nowhere`
- `tests-python` / `pytest:network` (local-live-internet, exit 1): `tests/test_r114_r118_real_fixes.py::test_live_keyless_llm_and_translation`
- `py-scripts` / `ruff:py-scripts` (local-test, exit 1): `docs/audit/M6/logs/raw/120_r113_analysis.py`, `docs/audit/M6/scripts/r105_ast_compare.py`, `docs/audit/M6/scripts/r105_scale_measure.py`
- `frontend-root` / `frontend-root:tsc` (local-test, exit 2): 
- `deploy-ci` (فجوة): Render deployment not exercised (no deploy credentials); render.yaml only parsed and self-checked
- `evidence-data` / `data:parse` (local-test, exit 1): `docs/audit/M6/logs/raw/09_npm_outdated_root.json`, `docs/audit/M6/logs/raw/10_npm_outdated_web.json`
- `evidence-logs` / `integrity` (local-test, exit 1): `docs/audit/M6/logs/79_r086_before.log`

## الاختبارات حسب الفئة (TEST_MATRIX_AR.md)

| الفئة | الوضع | ملفات | pass | fail | skip | xfail | errors |
|---|---|---|---|---|---|---|---|
| contract | real | 9 | 146 | 0 | 0 | 0 | 0 |
| contract | specialists | 9 | 146 | 0 | 0 | 0 | 0 |
| integration | real | 25 | 529 | 0 | 0 | 0 | 0 |
| integration | specialists | 25 | 526 | 3 | 0 | 0 | 0 |
| live | real | 1 | 0 | 0 | 4 | 0 | 0 |
| live | specialists | 1 | 0 | 0 | 4 | 0 | 0 |
| unit | real | 15 | 381 | 0 | 0 | 0 | 0 |
| unit | specialists | 15 | 381 | 0 | 0 | 0 | 0 |

## CI على SHA نفسه

تشغيل 39005652192 عند `573793c1f36afd77f3b79247ab9e2d978a397b62`: **success** — audit=success, build=success, docker=success, network=success, publish=skipped, quality=success, storage=success, test=success.

## حدود

- `local-test` = أُعيد تشغيله في هذا التشغيل؛ `local-operational` = خدمات حقيقية محلية (PostgreSQL/PostgREST)؛ `local-live-internet` = الإنترنت الحقيقي وقت التشغيل (خارج البصمة)؛ `ci` = نتيجة GitHub Actions على SHA نفسه (لم يُعِد هذا التشغيل إنتاجها)؛ `not-run` = لم يُنفذ.
- خط الأساس لا يثبت جاهزية إنتاجية ولا أهداف السعة (10,000 وكيل / 1,000,000 مهمة / 10,000 أداة).
