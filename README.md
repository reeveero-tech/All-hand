# AGOS Agent Civilization

AGOS is a real-first AI-agent civilization: the user submits a goal, the
civilization plans the stages, assigns specialist roles, executes real tools,
and returns a combined result.

## What is real now

- 20 in-house specializations backed by deterministic runtime handlers.
- 1,000 local workers by default: 20 specializations × 50 executable agents.
- 299+ curated open-source agent/framework entries with source URLs.
- Automatic routing: users do not choose a worker manually.
- Real operations include AST parsing, Python execution, SQLite execution,
  DNS/HTTP probes, statistics, tokenization, runtime metrics, and web-backed
  research where network access is available.
- FastAPI backend, React dashboard, Dockerfile, and Render blueprint.

## Run locally

```bash
pip install -r requirements.txt
python run.py
# or
uvicorn server:app --host 0.0.0.0 --port 8000
```

Optional environment variables:

```bash
OPENROUTER_API_KEY=...   # enables LLM reasoning in addition to deterministic tools
LLM_MODEL=meta-llama/llama-3.3-70b-instruct:free
SUPABASE_URL=...         # optional persistence
SUPABASE_ANON_KEY=...
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
