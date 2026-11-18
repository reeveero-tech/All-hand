"""D-165: sub-items (R-206a…) carry a strict deps line like every M9/M10 item.

Before D-165 the deps-line regex captured only ``R-\\d+``, so a sub-item's
own line (``- الاعتماديات (R-206a): …``) was invisible and the item was
reported as having no deps line. The fix widens only the tag capture to the
id grammar already used for items and tokens; every strict rule still applies.
"""
import importlib.util
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("cse", ROOT / "scripts/check_status_evidence.py")
cse = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cse)


def _root(tmp_path, items: str, table: str) -> str:
    gov = tmp_path / "docs/governance"
    gov.mkdir(parents=True)
    (gov / "ROADMAP.md").write_text(items, encoding="utf-8")
    (gov / "PROJECT_STATE.md").write_text(
        "## حالة بنود M10\n\n| البند | الحالة | العنوان |\n|---|---|---|\n" + table + "\n", encoding="utf-8")
    (gov / "DECISIONS.md").write_text("", encoding="utf-8")
    for cmd in (["init", "-q"], ["add", "-A"],
                ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init"]):
        subprocess.run(["git", *cmd], cwd=tmp_path, check=True)
    return str(tmp_path)


def _item(rid, status, deps, branch=True):
    f = "- أدلة الحالة (R-154): branch=x\n" if branch and status == "in_progress" else ""
    d = f"- الاعتماديات ({deps[0]}): {deps[1]}\n" if deps else ""
    return f"#### {rid} — t\n- الحالة: **{status}**\n{f}{d}\n"


def _run(tmp_path, specs):
    items = "".join(_item(*s) for s in specs)
    table = "\n".join(f"| {s[0]} | `{s[1]}` | t |" for s in specs)
    return cse.check(_root(tmp_path, items, table))


def test_subitem_deps_line_is_read(tmp_path):
    v = _run(tmp_path, [
        ("R-200", "in_progress", ("R-200", "لا شيء")),
        ("R-200a", "in_progress", ("R-200a", "لا شيء")),
        ("R-200b", "planned", ("R-200b", "R-200a"))])
    assert v == []


def test_subitem_without_deps_line_still_rejected(tmp_path):
    v = _run(tmp_path, [("R-200", "in_progress", ("R-200", "لا شيء")), ("R-200a", "planned", None)])
    assert any("R-200a: M9 items must declare dependencies" in x for x in v)


def test_subitem_tag_mismatch_rejected(tmp_path):
    v = _run(tmp_path, [("R-200", "in_progress", ("R-200", "لا شيء")),
                        ("R-200a", "planned", ("R-200", "لا شيء"))])
    assert any("R-200a: deps line tag mismatch" in x for x in v)


def test_started_subitem_needs_its_deps_done(tmp_path):
    v = _run(tmp_path, [("R-200", "in_progress", ("R-200", "لا شيء")),
                        ("R-200a", "in_progress", ("R-200a", "R-200"))])
    assert any("R-200a: dependency R-200 is in_progress" in x for x in v)
