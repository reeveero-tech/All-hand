"""R-202 (D-151): AGOS v2 contracts and data model.

Two layers, one set of rules:

* offline (always runs): the examples validate against the contracts, the
  committed JSON Schemas match the code, every invalid case is rejected by the
  contract, the SQL columns equal the contract fields, every table has RLS;
* live (``V2_SCHEMA_ADMIN_URI`` = a PostgreSQL superuser URI with pgvector —
  CI job ``storage``): a fresh database is created, scripts/v2/supabase_auth_stub.sql
  and docs/sql/v2/001_core.sql are applied, the example scenario is inserted
  from the contract JSON (jsonb_populate_record) and read back into the
  contracts, the database rejects the SAME invalid cases as the contracts,
  workspaces are isolated by RLS, and task_events is append-only.

The live part uses ``psql`` (no new Python dependency).
"""
from __future__ import annotations

import copy
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import uuid

import pytest
from pydantic import ValidationError

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agos_v2 import contracts as c  # noqa: E402

SQL = ROOT / "docs" / "sql" / "v2" / "001_core.sql"
STUB = ROOT / "scripts" / "v2" / "supabase_auth_stub.sql"
EX = ROOT / "docs" / "governance" / "contracts" / "v2" / "examples"
ADMIN_URI = os.environ.get("V2_SCHEMA_ADMIN_URI", "")


def ex(name):
    return json.loads((EX / f"{name}.json").read_text(encoding="utf-8"))


# ------------------------------------------------------------ invalid cases
# (contract, mutation, why). Each must be rejected by the contract AND by the
# database (live part) — the two layers agree.

def _task(**kw):
    t = ex("task")
    t["id"] = str(uuid.uuid4())
    t["idempotency_key"] = None
    t.update(kw)
    return t


def _step(**kw):
    s = copy.deepcopy(ex("steps")[1])
    s["id"] = str(uuid.uuid4())
    s["seq"] = 100 + uuid.uuid4().int % 1_000_000
    s.update(kw)
    return s


def _artifact(**kw):
    a = ex("artifact")
    a["id"] = str(uuid.uuid4())
    a.update(kw)
    return a


def _message(**kw):
    m = copy.deepcopy(ex("messages")[0])
    m["id"] = str(uuid.uuid4())
    m["seq"] = 100 + uuid.uuid4().int % 1_000_000
    m.update(kw)
    return m


INVALID = [
    ("task", lambda: _task(verified_at=None), "completed without verification"),
    ("task", lambda: _task(status="failed", verified_at=None, error=None), "failed without error"),
    ("task", lambda: _task(status="running", verified_at=None), "running with finished_at"),
    ("task", lambda: _task(status="queued", verified_at=None, finished_at=None, attempts=4),
     "attempts > max_attempts"),
    ("task", lambda: _task(status="done"), "unknown status"),
    ("task", lambda: _task(title=""), "empty title"),
    ("step", lambda: _step(verification={**ex("steps")[1]["verification"], "passed": False}),
     "succeeded with a failed verification"),
    ("step", lambda: _step(verification=None), "succeeded without verification"),
    ("step", lambda: (lambda s: {**s, "depends_on": [s["id"]]})(_step()), "depends on itself"),
    ("step", lambda: _step(status="failed", verification=None, error=None), "failed without error"),
    ("step", lambda: _step(status="running", verification=None), "running with finished_at"),
    ("step", lambda: _step(kind="think"), "unknown kind"),
    ("artifact", lambda: _artifact(storage_path="ws/answer.md"), "inline and storage both set"),
    ("artifact", lambda: _artifact(inline_content=None), "no content location"),
    ("artifact", lambda: _artifact(sha256="abc"), "bad sha256"),
    ("message", lambda: _message(role="robot"), "unknown role"),
]


# =================================================================== offline

def test_examples_validate_against_the_contracts():
    c.Task.model_validate(ex("task"))
    for s in ex("steps"):
        c.Step.model_validate(s)
    c.AgentSpec.model_validate(ex("agent_spec"))
    c.Artifact.model_validate(ex("artifact"))
    for m in ex("messages"):
        c.Message.model_validate(m)


def test_the_five_required_contracts_exist():
    assert set(c.CONTRACTS) == {"task", "step", "agent_spec", "artifact", "message"}


def test_committed_v2_schemas_match_the_code():
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "export_v2_contract_schemas.py"),
                        "--check"], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


def test_schema_files_carry_version_and_rules():
    for name in c.CONTRACTS:
        s = json.loads((ROOT / f"docs/governance/contracts/v2/{name}.v1.schema.json").read_text())
        assert s["x-contract-version"] == 1 and s["$id"] == f"agos://contracts/v2/{name}/v1"
        assert s["x-rules"] and s.get("additionalProperties") is False


def test_unknown_field_is_rejected():
    with pytest.raises(ValidationError):
        c.Task.model_validate({**ex("task"), "surprise": 1})


@pytest.mark.parametrize("name,make,why", INVALID, ids=[w for _, _, w in INVALID])
def test_contract_rejects_invalid(name, make, why):
    with pytest.raises(ValidationError):
        c.CONTRACTS[name].model_validate(make())


def _sql_columns() -> dict:
    """table -> column names, read from the create table statements."""
    text = SQL.read_text(encoding="utf-8")
    out = {}
    for m in re.finditer(r"create table agos\.(\w+) \((.*?)\n\);", text, re.S):
        cols = []
        for line in m.group(2).splitlines():
            # a column starts at exactly two spaces; continuation lines are deeper
            w = re.match(r"  ([a-z_][a-z0-9_]*)\s", line)
            if w and w.group(1) not in {"unique", "foreign", "check", "primary", "references"}:
                cols.append(w.group(1))
        out[m.group(1)] = cols
    return out


def test_sql_columns_equal_contract_fields():
    cols = _sql_columns()
    for name, table in c.TABLES.items():
        assert cols[table] == list(c.CONTRACTS[name].model_fields), (name, table)


def test_every_table_has_rls_and_a_workspace():
    text = SQL.read_text(encoding="utf-8")
    cols = _sql_columns()
    assert len(cols) == 10
    for table, columns in cols.items():
        assert f"alter table agos.{table:<17} enable row level security" in text or \
            f"alter table agos.{table} enable row level security" in text, table
        assert f"on agos.{table} for" in text, f"no policy on {table}"
        assert "workspace_id" in columns or table == "workspaces", table
    assert "to anon" not in text  # anon gets nothing


# ====================================================================== live

live = pytest.mark.skipif(not ADMIN_URI or not shutil.which("psql"),
                          reason="live v2 schema test: set V2_SCHEMA_ADMIN_URI "
                                 "(PostgreSQL + pgvector superuser) — runs in CI job storage")

DB = "agos_v2_r202_test"


def _uri(db):
    return re.sub(r"/[^/?]*(\?|$)", f"/{db}\\1", ADMIN_URI, count=1) if ADMIN_URI.count("/") >= 3 \
        else ADMIN_URI


def psql(sql: str, db: str = DB, check: bool = True, **vars_):
    args = ["psql", _uri(db), "-X", "-q", "-At", "-v", "ON_ERROR_STOP=1"]
    for k, v in vars_.items():
        args += ["-v", f"{k}={v}"]
    r = subprocess.run(args, input=sql, capture_output=True, text=True)
    if check and r.returncode != 0:
        raise AssertionError(f"psql failed: {r.stderr}\n--- sql ---\n{sql[:2000]}")
    return r


def ins(table: str, row: dict, check: bool = True):
    return psql(f"insert into agos.{table} select * from "
                f"jsonb_populate_record(null::agos.{table}, :'j'::jsonb);",
                check=check, j=json.dumps(row))


@pytest.fixture(scope="module")
def db():
    psql(f"drop database if exists {DB};", db="postgres")
    psql(f"create database {DB};", db="postgres")
    r1 = subprocess.run(["psql", _uri(DB), "-X", "-q", "-v", "ON_ERROR_STOP=1", "-f", str(STUB)],
                        capture_output=True, text=True)
    r2 = subprocess.run(["psql", _uri(DB), "-X", "-q", "-v", "ON_ERROR_STOP=1", "-f", str(SQL)],
                        capture_output=True, text=True)
    assert r1.returncode == 0, r1.stderr
    assert r2.returncode == 0, r2.stderr
    sc = ex("scenario")
    psql("insert into agos.workspaces (id, name) values (:'w', :'n');",
         w=sc["workspace"]["id"], n=sc["workspace"]["name"])
    psql("insert into agos.workspace_members (workspace_id, user_id, role) values (:'w', :'u', 'owner');",
         w=sc["member"]["workspace_id"], u=sc["member"]["user_id"])
    psql("insert into agos.conversations (id, workspace_id, created_by, title) values (:'c', :'w', :'u', :'t');",
         c=sc["conversation"]["id"], w=sc["workspace"]["id"], u=sc["member"]["user_id"],
         t=sc["conversation"]["title"])
    spec = ex("agent_spec")
    psql("insert into agos.agent_specs (id, workspace_id, slug, version, name, status, spec) "
         "values (:'id', null, :'slug', :'v', :'name', 'active', :'spec'::jsonb);",
         id=sc["agent_spec_id"], slug=spec["slug"], v=spec["version"], name=spec["name"],
         spec=json.dumps(spec))
    ins("tasks", ex("task"))
    for s in ex("steps"):
        ins("steps", s)
    ins("artifacts", ex("artifact"))
    for m in ex("messages"):
        ins("messages", m)
    yield sc
    psql(f"drop database if exists {DB};", db="postgres")


@live
def test_live_schema_applied_with_pgvector(db):
    r = psql("select count(*) from information_schema.tables where table_schema = 'agos';"
             "select extname from pg_extension where extname = 'vector';"
             "select count(*) from pg_tables where schemaname = 'agos' and rowsecurity;")
    assert r.stdout.split() == ["10", "vector", "10"]


@live
def test_live_columns_equal_contract_fields(db):
    for name, table in c.TABLES.items():
        r = psql("select column_name from information_schema.columns where table_schema = 'agos' "
                 f"and table_name = '{table}' order by ordinal_position;")
        assert r.stdout.split() == list(c.CONTRACTS[name].model_fields), table


@live
def test_live_rows_round_trip_into_the_contracts(db):
    for name, table in c.TABLES.items():
        r = psql(f"select row_to_json(t) from agos.{table} t order by created_at, id;")
        rows = [json.loads(line) for line in r.stdout.splitlines()]
        assert rows, table
        for row in rows:
            c.CONTRACTS[name].model_validate(row)
    r = psql("select spec from agos.agent_specs;")
    c.AgentSpec.model_validate(json.loads(r.stdout))


@live
@pytest.mark.parametrize("name,make,why", INVALID, ids=[w for _, _, w in INVALID])
def test_live_database_rejects_the_same_invalid_cases(db, name, make, why):
    table = {"task": "tasks", "step": "steps", "artifact": "artifacts", "message": "messages"}[name]
    r = ins(table, make(), check=False)
    assert r.returncode != 0, f"database accepted: {why}"
    assert re.search(r"violates|invalid input|value too long", r.stderr), r.stderr


@live
def test_live_valid_mutation_is_accepted(db):
    # control: the case generators themselves produce insertable rows
    assert ins("tasks", _task(), check=False).returncode == 0
    assert ins("steps", _step(), check=False).returncode == 0


@live
def test_live_child_cannot_point_into_another_workspace(db):
    other = str(uuid.uuid4())
    psql("insert into agos.workspaces (id, name) values (:'w', 'other');", w=other)
    r = ins("steps", _step(workspace_id=other), check=False)
    assert r.returncode != 0 and "foreign key" in r.stderr


def _as_user(uid: str, sql: str, check: bool = False):
    return psql("set role authenticated;"
                f"select set_config('request.jwt.claims', '{json.dumps({'sub': uid})}', false);"
                + sql, check=check)


@live
def test_live_rls_isolates_workspaces(db):
    owner = db["member"]["user_id"]
    stranger = str(uuid.uuid4())
    ws2 = str(uuid.uuid4())
    psql("insert into agos.workspaces (id, name) values (:'w', 'stranger ws');"
         "insert into agos.workspace_members values (:'w', :'u', 'owner', now());", w=ws2, u=stranger)
    mine = _as_user(owner, "select count(*) from agos.tasks;", check=True).stdout.split()[-1]
    theirs = _as_user(stranger, "select count(*) from agos.tasks;", check=True).stdout.split()[-1]
    assert int(mine) >= 1 and theirs == "0"
    assert _as_user(stranger, "select count(*) from agos.messages;", check=True).stdout.split()[-1] == "0"
    # the stranger cannot write into the owner's workspace
    t = _task()
    r = _as_user(stranger, "insert into agos.tasks select * from jsonb_populate_record("
                 f"null::agos.tasks, '{json.dumps(t)}'::jsonb);")
    assert r.returncode != 0 and "row-level security" in r.stderr
    # platform agent specs (workspace_id null) are visible to everyone
    assert _as_user(stranger, "select count(*) from agos.agent_specs;", check=True).stdout.split()[-1] == "1"


@live
def test_live_anon_has_no_access(db):
    r = psql("set role anon; select count(*) from agos.tasks;", check=False)
    assert r.returncode != 0 and "permission denied" in r.stderr


@live
def test_live_task_events_are_append_only(db):
    t = ex("task")
    psql("insert into agos.task_events (workspace_id, task_id, type, data) "
         "values (:'w', :'t', 'task.completed', '{}');", w=t["workspace_id"], t=t["id"])
    r = psql("update agos.task_events set type = 'task.failed';", check=False)
    assert r.returncode != 0 and "append-only" in r.stderr


@live
def test_live_task_completion_requires_verification_on_update(db):
    t = _task(status="running", verified_at=None, finished_at=None)
    ins("tasks", t)
    r = psql("update agos.tasks set status = 'completed', finished_at = now() where id = :'id';",
             check=False, id=t["id"])
    assert r.returncode != 0 and "violates check constraint" in r.stderr
    psql("update agos.tasks set status = 'completed', finished_at = now(), verified_at = now() "
         "where id = :'id';", id=t["id"])
