"""R-171 batch 1 — unified tool registry: contract, CRUD/lookup/search,
uniqueness, schema validation, versioning, auditable results, and the
seeded internal handlers cross-checked against the frozen dispatcher.

Acceptance (P5_TOOL_ECOSYSTEM_AR.md §R-171): "CRUD/lookup/search contract،
uniqueness، schema validation، versioning، نتائج قابلة للتدقيق". These tests
prove the in-process registry only — no persistence, no execution, no
security enforcement (R-172), no routing (R-174)."""
from __future__ import annotations

import ast
import hashlib
import importlib
import inspect
import json
import pathlib
import threading

import pytest

from agent_civilization.core import internal_tools, tool_registry as tr
from agent_civilization.core.agents import real_dispatcher

ROOT = pathlib.Path(__file__).resolve().parents[1]
DRAFT = ROOT / "docs/governance/roadmaps/revival-scale/specs/tool_registry_contract.schema.json"


def _rec(**over):
    base = {
        "tool_id": "acme.echo", "name": "echo", "version": "1.0.0",
        "source": {"kind": "internal"}, "description": "echo input",
        "capabilities": ["text.echo"], "input_schema": {"type": "object"},
        "output_schema": {"type": "object"}, "auth_scope": [],
        "tenant_scope": "request_tenant", "risk_class": "low", "read_only": True,
        "idempotent": True, "requires_approval": False, "timeout_ms": 1000,
        "health_status": "unknown", "test_status": "not_tested",
        "implementation_ref": "json:dumps", "lifecycle_state": "registered",
    }
    base.update(over)
    return base


# -- contract ---------------------------------------------------------------

def test_contract_matches_the_draft_plus_declared_changes():
    draft = json.loads(DRAFT.read_text(encoding="utf-8"))
    fields = set(tr.ToolRecord.model_fields)
    # R-172 batch 3 (D-117): approval_ttl_s and approval_scopes are ADDED to
    # the contract (optional; absent on every record stored before it) —
    # the approval POLICY fields, declared here like every other change.
    declared_additions = {"approval_ttl_s", "approval_scopes"}
    assert fields == set(draft["properties"]) | declared_additions, \
        fields ^ set(draft["properties"])
    required = {n for n, f in tr.ToolRecord.model_fields.items() if f.is_required()}
    # D-108: implementation_ref becomes required; nothing else changes.
    assert required == set(draft["required"]) | {"implementation_ref"}
    for enum_field in ("tenant_scope", "risk_class", "health_status", "test_status",
                       "lifecycle_state"):
        draft_enum = set(draft["properties"][enum_field]["enum"])
        rec = _rec()
        for value in draft_enum:
            tr.ToolRecord.model_validate({**rec, enum_field: value})
        with pytest.raises(Exception):
            tr.ToolRecord.model_validate({**rec, enum_field: "not-a-value"})


@pytest.mark.parametrize("bad", [
    {"extra_field": 1},
    {"tool_id": "a b"},
    {"tool_id": "ab"},
    {"version": "1.0"},
    {"version": "01.0.0"},
    {"input_schema": {}},
    {"output_schema": {"properties": {}}},
    {"capabilities": []},
    {"capabilities": ["x", "x"]},
    {"capabilities": [" "]},
    {"timeout_ms": 0},
    {"implementation_ref": "no_colon"},
    {"source": {"kind": "ftp"}},
    {"risk_class": "none"},
])
def test_invalid_records_are_refused_and_audited(bad):
    reg = tr.ToolRegistry()
    with pytest.raises(tr.InvalidRecord):
        reg.register(_rec(**bad), actor="t")
    assert len(reg) == 0
    (entry,) = reg.audit()
    assert entry["op"] == "register_refused" and entry["reason"] == "invalid_record"
    assert entry["fields"], entry


def test_actor_is_required():
    with pytest.raises(tr.InvalidRecord):
        tr.ToolRegistry().register(_rec(), actor="")


# -- create / read / uniqueness / versioning ----------------------------------

def test_register_get_latest_and_versions():
    reg = tr.ToolRegistry()
    reg.register(_rec(), actor="t")
    reg.register(_rec(version="1.2.0"), actor="t")
    reg.register(_rec(version="1.10.0"), actor="t")     # numeric, not lexical order
    assert reg.get("acme.echo").version == "1.10.0"
    assert reg.get("acme.echo", "1.2.0").version == "1.2.0"
    assert reg.versions("acme.echo") == ["1.0.0", "1.2.0", "1.10.0"]
    with pytest.raises(tr.ToolNotFound):
        reg.get("acme.echo", "9.9.9")
    with pytest.raises(tr.ToolNotFound):
        reg.get("missing.tool")


def test_duplicate_key_is_refused_versions_are_immutable():
    reg = tr.ToolRegistry()
    reg.register(_rec(), actor="t")
    with pytest.raises(tr.DuplicateTool):
        reg.register(_rec(description="different"), actor="t")
    assert reg.get("acme.echo").description == "echo input"


def test_versions_must_increase():
    reg = tr.ToolRegistry()
    reg.register(_rec(version="2.0.0"), actor="t")
    with pytest.raises(tr.VersionConflict):
        reg.register(_rec(version="1.9.9"), actor="t")


def test_alias_of_an_implementation_is_not_a_new_tool():
    reg = tr.ToolRegistry()
    reg.register(_rec(), actor="t")
    with pytest.raises(tr.AliasConflict):
        reg.register(_rec(tool_id="acme.echo2", name="echo2"), actor="t")
    reg.register(_rec(version="1.1.0"), actor="t")       # same tool, new version: fine
    assert len(reg) == 2


def test_new_records_cannot_arrive_certified_or_validated():
    reg = tr.ToolRegistry()
    with pytest.raises(tr.InvalidRecord):
        reg.register(_rec(lifecycle_state="certified"), actor="t")
    with pytest.raises(tr.InvalidRecord):
        reg.register(_rec(lifecycle_state="validated"), actor="t")
    with pytest.raises(tr.InvalidRecord):
        reg.register(_rec(test_status="certified"), actor="t")


def test_concurrent_registration_of_one_key_has_exactly_one_winner():
    reg = tr.ToolRegistry()
    results = []
    barrier = threading.Barrier(16)

    def go():
        barrier.wait()
        try:
            reg.register(_rec(), actor="t")
            results.append("ok")
        except tr.DuplicateTool:
            results.append("dup")
    threads = [threading.Thread(target=go) for _ in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results.count("ok") == 1 and results.count("dup") == 15
    assert len(reg) == 1


# -- update / lifecycle ------------------------------------------------------

def test_only_operational_fields_are_mutable():
    reg = tr.ToolRegistry()
    reg.register(_rec(), actor="t")
    with pytest.raises(tr.InvalidRecord):
        reg.update_status("acme.echo", "1.0.0", actor="t", description="sneaky")
    with pytest.raises(tr.InvalidRecord):
        reg.update_status("acme.echo", "1.0.0", actor="t", risk_class="low")
    rec = reg.update_status("acme.echo", "1.0.0", actor="t", health_status="healthy")
    assert rec.health_status == "healthy"


def test_lifecycle_graph_and_certification_rule():
    reg = tr.ToolRegistry()
    reg.register(_rec(), actor="t")
    with pytest.raises(tr.TransitionRefused):     # skip validated
        reg.update_status("acme.echo", "1.0.0", actor="t", lifecycle_state="certified",
                          test_status="certified")
    reg.update_status("acme.echo", "1.0.0", actor="t", lifecycle_state="validated",
                      test_status="contract_pass")
    with pytest.raises(tr.TransitionRefused):     # certified needs certified tests
        reg.update_status("acme.echo", "1.0.0", actor="t", lifecycle_state="certified")
    rec = reg.update_status("acme.echo", "1.0.0", actor="t", lifecycle_state="certified",
                            test_status="certified")
    assert rec.lifecycle_state == "certified"
    with pytest.raises(tr.TransitionRefused):
        reg.update_status("acme.echo", "1.0.0", actor="t", lifecycle_state="registered")


def test_every_declared_move_targets_a_known_state():
    states = set(tr.Lifecycle.__args__)
    assert set(tr.LIFECYCLE_MOVES) == states
    for frm, tos in tr.LIFECYCLE_MOVES.items():
        assert set(tos) <= states and frm not in tos


# -- delete --------------------------------------------------------------------

def test_remove_leaves_a_tombstone_against_silent_replacement():
    reg = tr.ToolRegistry()
    reg.register(_rec(), actor="t")
    reg.remove("acme.echo", "1.0.0", actor="t")
    assert len(reg) == 0
    with pytest.raises(tr.ToolNotFound):
        reg.get("acme.echo")
    with pytest.raises(tr.VersionConflict):
        reg.register(_rec(description="replaced"), actor="t")
    reg.register(_rec(), actor="t")                       # identical content may return
    with pytest.raises(tr.ToolNotFound):
        reg.remove("acme.echo", "7.0.0", actor="t")


# -- search ----------------------------------------------------------------------

def _populated():
    reg = tr.ToolRegistry()
    reg.register(_rec(tool_id="b.net", name="net", capabilities=["net.probe"],
                      risk_class="medium", implementation_ref="json:loads"), actor="t")
    reg.register(_rec(tool_id="a.echo", implementation_ref="json:dumps"), actor="t")
    reg.register(_rec(tool_id="a.echo", version="1.1.0", implementation_ref="json:dumps",
                      description="echo v2"), actor="t")
    reg.register(_rec(tool_id="c.exec", name="exec", capabilities=["code.execute"],
                      risk_class="high", read_only=False,
                      implementation_ref="os:getcwd"), actor="t")
    reg.register(_rec(tool_id="d.off", name="off", implementation_ref="os:getpid"), actor="t")
    reg.update_status("d.off", "1.0.0", actor="t", lifecycle_state="disabled")
    return reg


def test_search_filters_are_exact_and_deterministic():
    reg = _populated()
    ids = lambda rs: [(r.tool_id, r.version) for r in rs]   # noqa: E731
    assert ids(reg.search()) == [("a.echo", "1.1.0"), ("b.net", "1.0.0"), ("c.exec", "1.0.0")]
    assert ids(reg.search(latest_only=False)) == [
        ("a.echo", "1.0.0"), ("a.echo", "1.1.0"), ("b.net", "1.0.0"), ("c.exec", "1.0.0")]
    assert ids(reg.search(capability="net.probe")) == [("b.net", "1.0.0")]
    assert ids(reg.search(capability="net")) == []          # exact, not prefix
    assert ids(reg.search(max_risk="medium")) == [("a.echo", "1.1.0"), ("b.net", "1.0.0")]
    assert ids(reg.search(read_only=False)) == [("c.exec", "1.0.0")]
    assert ids(reg.search(text="ECHO V2")) == [("a.echo", "1.1.0")]
    assert ids(reg.search(include_disabled=True, capability="text.echo")) == [
        ("a.echo", "1.1.0"), ("d.off", "1.0.0")]
    assert ids(reg.search(limit=1)) == [("a.echo", "1.1.0")]
    with pytest.raises(tr.InvalidRecord):
        reg.search(max_risk="extreme")


# -- audit -----------------------------------------------------------------------

def test_audit_trail_is_ordered_and_digests_are_reproducible():
    reg = tr.ToolRegistry(clock=lambda: 42.0)
    rec = reg.register(_rec(), actor="alice")
    with pytest.raises(tr.DuplicateTool):
        reg.register(_rec(), actor="mallory")
    upd = reg.update_status("acme.echo", "1.0.0", actor="bob", health_status="degraded")
    reg.remove("acme.echo", "1.0.0", actor="carol")
    log = reg.audit()
    assert [e["seq"] for e in log] == [1, 2, 3, 4]
    assert [(e["op"], e["actor"]) for e in log] == [
        ("register", "alice"), ("register_refused", "mallory"), ("update", "bob"),
        ("remove", "carol")]
    # R-172 batch 3 (D-117): the digest excludes the additive approval-policy
    # fields (see ToolRecord.DIGEST_EXCLUDE) — reproducible means: the SAME
    # canonical form the registry digests, not a naive full dump.
    canonical = json.dumps(
        rec.model_dump(mode="json", exclude=tr.ToolRecord.DIGEST_EXCLUDE),
        sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    assert log[0]["digest"] == hashlib.sha256(canonical.encode()).hexdigest() == rec.digest()
    assert log[2]["prev_digest"] == rec.digest() and log[2]["digest"] == upd.digest()
    assert log[3]["digest"] == upd.digest()
    log[0]["actor"] = "tampered"                                 # a copy, not the trail
    assert reg.audit()[0]["actor"] == "alice"


# -- seeded internal tools vs the frozen dispatcher ---------------------------

def _live_import_reach(module, func_name: str, seen: set) -> set:
    """R-172 batch 2: the reach of a function imported INSIDE a handler body
    (a dynamic import the old cross-check could not see — the exact hole that
    let data_processor's model call drift from its record, log 337). Follows
    the imported function's own names, its same-module closures, and ITS
    dynamic imports, so a handler cannot hide a network/model call one import
    away from the frozen dispatcher."""
    key = (getattr(module, "__name__", "?"), func_name)
    if key in seen:
        return set()
    seen.add(key)
    try:
        src = inspect.getsource(module)
    except (OSError, TypeError):
        return set()
    tree = ast.parse(src)
    funcs = {n.name: n for n in tree.body
             if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    node = funcs.get(func_name)
    if node is None:
        return set()
    names = set()
    imports = []
    for x in ast.walk(node):
        if isinstance(x, ast.Name):
            names.add(x.id)
        elif isinstance(x, ast.Attribute):
            names.add(x.attr)
        elif isinstance(x, ast.ImportFrom) and isinstance(x.module, str):
            imports.append(x)
    for imp in imports:
        try:
            mod = importlib.import_module(imp.module)
        except Exception:  # noqa: BLE001 — an unresolvable import adds nothing
            continue
        for alias in imp.names:
            names |= _live_import_reach(mod, alias.name, seen)
    for n in sorted(names & set(funcs)):
        names |= _live_import_reach(module, n, seen)
    return names


def _dispatcher_reach():
    src = pathlib.Path(real_dispatcher.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    funcs = {n.name: n for n in tree.body
             if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    refs = {}
    for name, node in funcs.items():
        names = set()
        live_imports = []
        for x in ast.walk(node):
            if isinstance(x, ast.Name):
                names.add(x.id)
            elif isinstance(x, ast.Attribute):
                names.add(x.attr)
            elif isinstance(x, ast.ImportFrom) and isinstance(x.module, str):
                live_imports.append(x)
        for imp in live_imports:
            try:
                mod = importlib.import_module(imp.module)
            except Exception:  # noqa: BLE001 — unresolvable imports add nothing
                continue
            for alias in imp.names:
                names |= _live_import_reach(mod, alias.name, set())
        refs[name] = names

    def closure(name, seen):
        if name in seen:
            return set()
        seen.add(name)
        out = set(refs[name])
        for r in refs[name]:
            if r in funcs:
                out |= closure(r, seen)
            if r == "_HANDLERS":
                for h in real_dispatcher._HANDLERS.values():
                    out |= closure(h.__name__, seen)
        return out
    return {spec: closure(fn.__name__, set()) for spec, fn in real_dispatcher._HANDLERS.items()}


def test_seed_covers_exactly_the_dispatcher_handlers():
    recs = internal_tools.internal_tool_records()
    assert {r.name for r in recs} == set(real_dispatcher._HANDLERS)
    for r in recs:
        assert tr.resolve_implementation(r.implementation_ref) is \
            real_dispatcher._HANDLERS[r.name], r.implementation_ref


def test_seed_declarations_match_static_reach_of_the_frozen_code():
    reach = _dispatcher_reach()
    # only the LATEST version of each tool is a live declaration — the
    # legacy rows preserved by R-172 (log 337) are history, kept byte-exact
    # on purpose, and are not re-asserted here.
    latest: dict = {}
    for r in internal_tools.internal_tool_records():
        cur = latest.get(r.name)
        if cur is None or tr._vkey(r.version) > tr._vkey(cur.version):
            latest[r.name] = r
    for r in latest.values():
        names = reach[r.name]
        net = "safe_urlopen" in names
        model = bool({"_llm_text", "get_llm"} & names)
        execs = "run_sandboxed_python" in names
        assert (internal_tools.NET in r.side_effects) is net, (r.name, "network")
        assert bool(r.network_allowlist) is net, (r.name, "allowlist")
        assert (internal_tools.MODEL in r.side_effects) is model, (r.name, "model")
        assert (internal_tools.EXEC in r.side_effects) is execs, (r.name, "exec")
        if execs:
            assert r.risk_class in ("high", "critical"), r.name
        if not (net or model or execs):
            assert r.risk_class == "low", r.name


def test_seeded_entries_claim_nothing_about_quality():
    reg = internal_tools.seeded_registry()
    # R-172 batches 2-3: the store holds one record per handler PLUS the
    # legacy rows (data_processor 1.0.0 and modifier 1.0.0 — their
    # corrections ship as newer versions); latest_only search still maps
    # 1:1 onto the handlers.
    assert len(reg) == (len(real_dispatcher._HANDLERS)
                        + len(internal_tools._LEGACY_ROWS)) == 22
    assert len(reg.search(limit=100)) == len(real_dispatcher._HANDLERS) == 20
    for r in reg.search(include_disabled=True, limit=100):
        assert (r.lifecycle_state, r.test_status, r.health_status) == \
            ("registered", "not_tested", "unknown"), r.tool_id
    assert [e["op"] for e in reg.audit()] == ["register"] * 22
    assert [r.name for r in reg.search(capability="code.execute")] == ["test_runner"]
