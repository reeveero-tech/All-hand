"""D-164: the one dated exception in the full web/ npm audit (scripts/check_npm_audit.py)."""
from __future__ import annotations

import copy
import datetime as dt
import importlib.util
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("check_npm_audit", ROOT / "scripts" / "check_npm_audit.py")
cna = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cna)

PICO = {"source": 1247710, "name": "picomatch", "dependency": "picomatch", "severity": "high",
        "title": "picomatch vulnerable to ReDoS through single-branch repeated extglobs",
        "url": "https://github.com/advisories/GHSA-fc5q-39xc-vjpf", "range": "<=4.0.7"}
# the real shape of `npm audit --json` in web/ on 2026-11-18 (5 high, one advisory)
REPORT = {"auditReportVersion": 2, "vulnerabilities": {
    "picomatch": {"name": "picomatch", "severity": "high", "via": [PICO]},
    "fdir": {"name": "fdir", "severity": "high", "via": ["picomatch"]},
    "tinyglobby": {"name": "tinyglobby", "severity": "high", "via": ["fdir", "picomatch"]},
    "vite": {"name": "vite", "severity": "high", "via": ["picomatch", "tinyglobby"]},
    "@vitejs/plugin-react": {"name": "@vitejs/plugin-react", "severity": "high", "via": ["vite"]}},
    "metadata": {"vulnerabilities": {"high": 5, "critical": 0, "total": 5}}}
BEFORE = dt.date(2026, 11, 18)


def test_only_the_excepted_advisory_passes_with_a_note():
    errors, notes = cna.check(REPORT, BEFORE)
    assert errors == [] and any("GHSA-fc5q-39xc-vjpf" in n and "2026-12-18" in n for n in notes)


def test_any_other_high_or_critical_advisory_blocks():
    for sev in ("high", "critical"):
        r = copy.deepcopy(REPORT)
        r["vulnerabilities"]["vite"]["via"].append(
            {"source": 1, "name": "vite", "severity": sev, "url": "https://github.com/advisories/GHSA-aaaa-bbbb-cccc"})
        errors, _ = cna.check(r, BEFORE)
        assert len(errors) == 1 and "GHSA-aaaa-bbbb-cccc" in errors[0]


def test_moderate_and_low_do_not_block():
    r = copy.deepcopy(REPORT)
    r["vulnerabilities"]["vite"]["via"].append(
        {"source": 2, "name": "vite", "severity": "moderate", "url": "https://github.com/advisories/GHSA-mmmm-mmmm-mmmm"})
    assert cna.check(r, BEFORE)[0] == []


def test_the_exception_expires_by_itself():
    errors, _ = cna.check(REPORT, dt.date(2026, 12, 19))
    assert len(errors) == 1 and "expired" in errors[0]
    assert cna.check(REPORT, dt.date(2026, 12, 18))[0] == []      # the last allowed day


def test_the_same_advisory_in_another_package_blocks():
    r = copy.deepcopy(REPORT)
    r["vulnerabilities"]["react"] = {"name": "react", "severity": "high", "via": [dict(PICO, name="react")]}
    errors, _ = cna.check(r, BEFORE)
    assert len(errors) == 1 and "outside its exception" in errors[0]


def test_a_fixed_advisory_says_the_exception_can_go():
    clean = {"vulnerabilities": {}, "metadata": {"vulnerabilities": {"total": 0}}}
    errors, notes = cna.check(clean, BEFORE)
    assert errors == [] and any("no longer needed" in n for n in notes)


def test_bad_input_blocks(tmp_path, capsys):
    p = tmp_path / "a.json"
    p.write_text("npm ERR! network", encoding="utf-8")
    assert cna.main(["x", str(p)]) == 1
    p.write_text(json.dumps({"error": {"code": "ENOAUDIT"}}), encoding="utf-8")
    assert cna.main(["x", str(p)]) == 1
    p.write_text(json.dumps(REPORT), encoding="utf-8")
    assert cna.main(["x", str(p)]) == (0 if dt.date.today() <= dt.date(2026, 12, 18) else 1)


def test_ci_uses_the_checker_and_keeps_the_production_audit_strict():
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "npm audit --omit=dev --audit-level=high" in ci          # unchanged, no exception
    assert "python3 ../scripts/check_npm_audit.py" in ci
    assert "pip-audit -r requirements.lock" in ci
