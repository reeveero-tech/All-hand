"""R-200 — truth inventory (جرد الحقيقة الشامل).

Checks the two generators (scripts/build_truth_inventory.py and
scripts/build_route_inventory.py) and the committed snapshot under
docs/audit/M10/inventory/. Removal verdicts are proposals only; nothing is
deleted here (deletion is R-203 under D-064).
"""
import csv
import importlib.util
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INV = ROOT / "docs" / "audit" / "M10" / "inventory"
SNAPSHOT = "f19328877cc66b254b93337340517b466d51f662"
TRUTH_CSV = INV / "TRUTH_INVENTORY_f1932887.csv"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


truth = _load("build_truth_inventory")
routes = _load("build_route_inventory")

SHIM_BODY = "from ._templates.SPEC_base import generate_create\ncreate = generate_create(7)\n"


def _row(path, imported_by="", status="active"):
    return {"path": path, "owner_component": "x", "entrypoint_or_imported_by": imported_by,
            "inventory_status": status, "decision_no_delete": "KEEP"}


def _classify(rows):
    return {r["path"]: r for r in truth.classify(rows, lambda p: SHIM_BODY)}


def test_a_numbered_shim_is_fake_with_evidence():
    out = _classify([_row("agent_civilization/agents/specialists/analyst/analyst_0007.py")])
    r = out["agent_civilization/agents/specialists/analyst/analyst_0007.py"]
    assert r["verdict"] == "FAKE_REMOVE"
    assert "1 shim files" in r["evidence"] and "distinct bodies" in r["evidence"]
    assert truth.self_check(list(out.values())) == []


def test_the_second_frontend_is_unused_and_root_package_json_too():
    out = _classify([_row("src/routes/chat.tsx"), _row("vite.config.ts")])
    assert {r["verdict"] for r in out.values()} == {"UNUSED_REMOVE"}
    assert all(r["rule"] == "U01-src-frontend" for r in out.values())


def test_self_check_rejects_a_removal_without_evidence_and_a_missing_verdict():
    bad = [{"path": "a.py", "verdict": "UNUSED_REMOVE", "reason": "x", "evidence": ""},
           {"path": "b.py", "verdict": "", "reason": "", "evidence": ""}]
    errs = truth.self_check(bad)
    assert any("a.py" in e for e in errs) and any("b.py" in e for e in errs)


def test_a_truncated_importer_list_never_yields_unused():
    path = "agos_kernel/infrastructure/some_module.py"
    trunc = _row(path, "imported/loaded by 9: tests/a.py, tests/b.py, …")
    full = _row(path, "imported/loaded by 2: tests/a.py, agos_kernel/infrastructure/__init__.py")
    assert _classify([trunc])[path]["verdict"] == "KEEP"
    assert _classify([full])[path]["verdict"] == "UNUSED_REMOVE"


def test_a_runtime_importer_keeps_the_module():
    path = "agos_kernel/infrastructure/some_module.py"
    row = _row(path, "imported/loaded by 1: server.py")
    assert _classify([row])[path]["verdict"] == "KEEP"


def test_route_parser_on_the_real_repo():
    rows = routes.build(ROOT)
    by = {f'{r["method"]} {r["path"]}': r for r in rows}
    # 31 routes on server.py + 19 /api/v2 routes on the R-204 APIRouter
    # (agos_v2/accounts_api.py, prefix "/api/v2") + 7 R-205 provider-key routes
    # (agos_v2/byok.py, its own APIRouter, prefix "/api/v2") — the prefix is applied
    assert len(rows) == 57
    v2 = [r for r in rows if r["path"].startswith("/api/v2/")]
    assert len([r for r in v2 if r["source"].startswith("agos_v2/accounts_api.py:")]) == 19
    keys = [r for r in v2 if r["source"].startswith("agos_v2/byok.py:")]
    assert len(keys) == 7 and len(v2) == 26
    assert all("provider-keys" in r["path"] for r in keys)
    assert by["POST /api/v2/workspaces/{workspace_id}/provider-keys"]["used_by_web_ui"] == "yes"
    assert by["POST /api/v2/workspaces/{workspace_id}/provider-keys/{key_id}/test"]["used_by_web_ui"] == "yes"
    assert by["POST /api/v2/auth/login"]["used_by_web_ui"] == "yes"
    assert by["POST /api/v2/invitations/accept"]["used_by_web_ui"] == "yes"
    assert by["GET /{full_path:path}"]["used_by_web_ui"] == "no"
    assert by["POST /api/chat"]["used_by_web_ui"] == "yes"
    assert not any(r["used_by_web_ui"] == "yes" for r in rows if r["path"].startswith("/civilization"))
    # the undeployed src/ UI (its calls matched no route) was removed in R-203
    # (D-152, DEL-001); the deployed web/ UI has no orphan call
    assert not (ROOT / "src").exists()
    assert routes.unmatched_calls(ROOT / "web" / "src", rows) == []


def test_unprobed_routes_are_never_claimed_public():
    rows = routes.build(ROOT, {"GET /api/tasks": 401, "GET /api/agents": 200})
    by = {f'{r["method"]} {r["path"]}': r for r in rows}
    assert by["GET /api/tasks"]["requires_key_live"] == "yes"
    assert by["GET /api/agents"]["requires_key_live"] == "no"
    assert by["POST /api/chat"]["requires_key_live"].startswith("not probed")


def test_the_committed_snapshot_covers_every_tracked_path():
    have = subprocess.run(["git", "-C", str(ROOT), "cat-file", "-e", f"{SNAPSHOT}^{{commit}}"],
                          capture_output=True)
    with open(TRUTH_CSV, encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    assert truth.self_check(rows) == []
    summary = json.loads((INV / "TRUTH_SUMMARY_f1932887.json").read_text(encoding="utf-8"))
    assert summary["rows"] == len(rows) and summary["self_check"] == "PASS"
    assert summary["verdicts"]["FAKE_REMOVE"] == 1001
    if have.returncode != 0:  # shallow clone without the snapshot commit
        return
    tracked = subprocess.run(["git", "-C", str(ROOT), "ls-tree", "-r", "--name-only", SNAPSHOT],
                             capture_output=True, text=True, check=True).stdout.split("\n")
    assert {p for p in tracked if p} == {r["path"] for r in rows}


def test_the_report_states_removal_is_only_a_proposal():
    text = (INV / "REPORT_AR.md").read_text(encoding="utf-8")
    assert "اقتراح" in text and "R-203" in text and "D-064" in text
