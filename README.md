# AGOS Agent Civilization

AGOS is a real-first AI-agent civilization: the user submits a goal, the
civilization plans the stages, assigns specialist roles, executes real tools,
and returns a combined result.

> **Start here (2026-11-11, owner decision D-150):** read
> [`docs/governance/MEMORY.md`](docs/governance/MEMORY.md) (project memory, in
> Arabic). The project is being rebuilt under program **M10 — AGOS v2**
> ([roadmap](docs/governance/roadmaps/agos-v2/ROADMAP_V2_AR.md)). Every merge
> must update the memory, roadmap and state docs in the same batch.

## What is real now (honest summary)

- 20 in-house specializations backed by deterministic runtime handlers.
- The "1,000 local workers" are 20 handlers × 50 identical generated copies —
  not 1,000 distinct agents.
- 299+ open-source agent/framework entries are metadata (name, URL) only; none
  of them is invoked by the system.
- Chat routing picks one handler by keywords; multi-agent coordination exists
  only in the `/civilization/plan|execute` API, which the web UI does not use.
- Not declared production-ready.
- Real operations include AST parsing, Python execution, SQLite execution,
  DNS/HTTP probes, statistics, tokenization, runtime metrics, and web-backed
  research where network access is available.
- FastAPI backend, React dashboard, Dockerfile, and Render blueprint.

## Run locally

```bash
pip install -r requirements.txt
python run.py
# or (SEC-03: without keys the server only starts in an explicit development environment)
AGOS_ENV=development uvicorn server:app --host 0.0.0.0 --port 8000
```

Production (`AGOS_ENV` unset or `production`, as in `render.yaml` and the
Dockerfile) refuses to start without `AGOS_TENANT_KEYS` and/or `AGOS_API_KEY` —
see `docs/governance/OPERATIONS.md`.

Optional environment variables:

```bash
OPENROUTER_API_KEY=...   # enables LLM reasoning in addition to deterministic tools
# without it, a public key-free LLM is used (labelled keyless:<model>; AGOS_LLM_KEYLESS=0 disables) — D-051
LLM_MODEL=meta-llama/llama-3.3-70b-instruct:free
SUPABASE_URL=...         # optional persistence (apply docs/sql/001_init.sql first)
SUPABASE_SERVICE_KEY=... # server-side secret/service_role key (RLS on, no anon policies — D-039)
```

## API flow

- `POST /civilization/plan` receives `{ "message": "..." }` and returns the
auto-generated multi-stage plan.
- `POST /civilization/execute` receives the same message and executes all stages.
- `GET /civilization/registry` returns the open-source agent registry and total
addressable civilization scale.
- Legacy `/api/tasks` remains available for direct specialization calls.

## Deployment

Use `render.yaml` for a free Render web service or run the Dockerfile on any
container host. Set `OPENROUTER_API_KEY` in the host environment for LLM-backed
reasoning.

## الحوكمة (إلزامية)

يتبع هذا المستودع **قاعدة الدفعة الذرية**: لا يُدفع أو يُدمج أي تغيير — كوداً أو إعدادات أو بيانات أو قراراً أو تحليلاً أو توثيقاً — إلا إذا حدّثت الدفعة نفسُها حالة المشروع وخارطة الطريق والوثائق المتأثرة.
راجع [docs/governance/](docs/governance/) — ابدأ بـ
[PROJECT_CHARTER.md](docs/governance/PROJECT_CHARTER.md) و
[PROJECT_STATE.md](docs/governance/PROJECT_STATE.md) و
[ROADMAP.md](docs/governance/ROADMAP.md).

دليل التحقق وسجلات التنفيذ المحفوظة في
[docs/audit/](docs/audit/M0/).

للإفصاحات الأمنية راجع [SECURITY.md](SECURITY.md)؛ أما الوضع الأمني المقيس حالياً فموثق في [docs/governance/SECURITY.md](docs/governance/SECURITY.md).
