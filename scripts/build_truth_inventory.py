#!/usr/bin/env python3
"""R-200 (M10/P0, D-150): the truth inventory — what is real and what is not.

Usage::

    python3 scripts/build_truth_inventory.py <base_inventory.csv> <out.csv> [--summary out.json]

Input: the deterministic R-155 inventory (``scripts/build_file_inventory.py``)
of the SAME snapshot — one row per tracked file with git, import-graph,
entry-point and reference evidence. This script adds ONE verdict per file:

  KEEP           حقيقي ويُبقى            real and kept as is (or as history)
  REDESIGN       حقيقي ويُعاد تصميمه       real, but rebuilt/reshaped in M10
  FAKE_REMOVE    شكلي ويُزال             presents something that is not real
  UNUSED_REMOVE  غير مستخدم ويُزال        not used by the product

Rules are ordered; the FIRST matching rule decides (rule id in ``rule``).
Every *_REMOVE row must carry a reason and evidence (self-check). A verdict
of *_REMOVE is a PROPOSAL: nothing is deleted here — deletion only happens
through the R-203 mechanism with an owner decision (D-064). No network, no
GitHub API: the classification is reproducible from the CSV alone.

Exit codes: 0 ok; 1 self-check failed (row missing a verdict, *_REMOVE
without reason/evidence, duplicate path); 2 usage error.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import subprocess
import sys
from collections import Counter
from typing import Callable, Dict, List, Optional, Tuple

VERDICTS = ("KEEP", "REDESIGN", "FAKE_REMOVE", "UNUSED_REMOVE")
VERDICT_AR = {"KEEP": "حقيقي ويُبقى", "REDESIGN": "حقيقي ويُعاد تصميمه",
              "FAKE_REMOVE": "شكلي ويُزال", "UNUSED_REMOVE": "غير مستخدم ويُزال"}
COLUMNS = ["path", "area", "verdict", "verdict_ar", "rule", "reason", "evidence",
           "m10_item", "base_status", "base_decision"]

SHIM_RE = re.compile(r"^agent_civilization/agents/specialists/([a-z_]+)/\1_\d{4}\.py$")
ROOT_FRONTEND = {"vite.config.ts", "tsconfig.json", "components.json", "eslint.config.js",
                 "package.json", "package-lock.json", "public/robots.txt", ".lovable/plan.md"}
# Root docs that carry the R-056 banner «وثيقة تاريخية» (they describe an older
# architecture) and are referenced only by CHANGELOG/inventories. DEPLOY.md is
# NOT here: it is current (R-041) and referenced by OPERATIONS.md.
STALE_ROOT_DOCS = {"CIVILIZATION_MAP.md", "FINAL-ARCHITECTURE.md", "IMPLEMENTATION_PLAN.md",
                   "INTEGRITY-REPORT.md"}


def importers(base_row: Dict[str, str]) -> Tuple[Optional[int], List[str], bool]:
    """(count, shown importers, truncated) parsed from the R-155 evidence."""
    m = re.search(r"imported/loaded by (\d+): ([^|]*)", base_row.get("entrypoint_or_imported_by", ""))
    if not m:
        return 0, [], False
    shown = [s.strip() for s in m.group(2).replace("…", "").split(",") if s.strip()]
    return int(m.group(1)), shown, "…" in m.group(2)


def only_package_or_tests(path: str, base_row: Dict[str, str]) -> Optional[str]:
    """Evidence string if every importer is a test, a doc/audit script or the
    module's own package ``__init__`` (and the file is no entry point)."""
    ev = base_row.get("entrypoint_or_imported_by", "")
    if ev.startswith("ENTRY") or "ENTRY " in ev:
        return None
    n, shown, trunc = importers(base_row)
    if trunc or n != len(shown):
        return None  # cannot see every importer -> never claim non-use
    pkg_init = path.rsplit("/", 1)[0] + "/__init__.py"
    runtime = [s for s in shown if not (s.startswith(("tests/", "docs/")) or s == pkg_init)]
    if runtime:
        return None
    return f"importers({n})={', '.join(shown) or 'none'}; no runtime importer outside its own package __init__"


def shim_evidence(paths: List[str], git_show: Callable[[str], str]) -> str:
    bodies = Counter()
    for p in paths:
        spec = SHIM_RE.match(p).group(1)
        text = git_show(p)
        norm = re.sub(r"\d+", "N", text.replace(spec, "SPEC"))
        norm = re.sub(r"[A-Z][A-Za-z]*AgentN", "ClassN", norm)
        bodies[hashlib.sha1(norm.encode()).hexdigest()[:12]] += 1
    return (f"{len(paths)} shim files; distinct bodies after normalising the number and the "
            f"specialisation name = {len(bodies)}; each re-exports a generated class from "
            f"_templates via generate_create (R-105/D-049)")


Rule = Tuple[str, Callable[[str, Dict[str, str]], Optional[Tuple[str, str, str, str]]]]


def build_rules(ctx: Dict[str, str]) -> List[Rule]:
    def rx(pattern: str, verdict: str, reason: str, evidence: str, item: str):
        cre = re.compile(pattern)
        return lambda p, b: (verdict, reason, evidence, item) if cre.search(p) else None

    def heuristic_unused(prefixes: Tuple[str, ...], item: str):
        def f(p: str, b: Dict[str, str]):
            if not p.endswith(".py") or not p.startswith(prefixes) or p.endswith("__init__.py"):
                return None
            ev = only_package_or_tests(p, b)
            if ev is None:
                return None
            return ("UNUSED_REMOVE", "لا يستدعيه الخادم ولا أي مسار تشغيل؛ يصل إليه فقط ملف التهيئة لحزمته والاختبارات", ev, item)
        return f

    return [
        # ---- the illusions (the owner's direct complaint) ----------------------
        ("F01-shims", lambda p, b: ("FAKE_REMOVE",
            "واحدة من 1000 نسخة رقمية متطابقة تُعرض كـ«1000 وكيل نشط»؛ لا منطق خاص بها",
            ctx["shim_evidence"], "R-201/R-203") if SHIM_RE.match(p) else None),
        ("F02-oss-list", rx(r"^agent_civilization/registry/oss_agents\.py$", "FAKE_REMOVE",
            "قائمة 299 مشروعًا مفتوح المصدر بيانات وصفية فقط تُضاف إلى عدد الوكلاء ولا يُستدعى أي منها",
            "only consumers: registry/__init__ re-exports AGENTS/as_list/total_agents used by "
            "civilization_routes (/civilization/registry listing) and run.py (count banner); "
            "no dispatch path calls an OSS agent (grep: no invocation of OSSAgent anywhere)", "R-201/R-214")),
        # ---- second, undeployed frontend --------------------------------------
        ("U01-src-frontend", lambda p, b: ("UNUSED_REMOVE",
            "واجهة ثانية (TanStack/Lovable) لا تُنشر: Dockerfile وrender.yaml يبنيان web/ فقط",
            "R-155 gap: root TanStack Start app is built+linted by CI but NOT deployed by "
            "Dockerfile/render.yaml (they build web/); base_status=" + b.get("inventory_status", ""),
            "R-202/R-203") if (p.startswith("src/") or p in ROOT_FRONTEND) else None),
        # ---- stale root docs (claims not matching the code) ---------------------
        ("U02-stale-root-docs", lambda p, b: ("UNUSED_REMOVE",
            "وثيقة جذرية موسومة «وثيقة تاريخية» (R-056) تصف معمارية سابقة؛ تحل محلها docs/governance وMEMORY.md",
            "first lines carry the R-056 'وثيقة تاريخية' banner; R-155 consumer = "
            + (b.get("entrypoint_or_imported_by") or "none found")
            + "; textual refs only from CHANGELOG/OPEN_QUESTIONS and inventory snapshots",
            "R-201/R-203") if p in STALE_ROOT_DOCS else None),
        # ---- code not on any runtime path -------------------------------------
        ("U03-redis-queue", rx(r"^agos_kernel/infrastructure/redis_queue\.py$", "UNUSED_REMOVE",
            "طابور Redis غير موجود على مسار الطلبات (الخادم يستخدم الذاكرة وSupabase)",
            "agent_civilization/storage/supabase_store.py: 'Redis (agos_kernel.infrastructure.redis_queue) "
            "is not on the request path'; importers: infrastructure/__init__, health.py, tests", "R-202/R-203")),
        ("U04-agent-factory", rx(r"^agos_kernel/agent_production/agent_factory\.py$", "UNUSED_REMOVE",
            "مصنع وكلاء قديم لا يستخدمه الخادم؛ الوكلاء الحاليون من _templates",
            "importers: agos_kernel/agent_production/__init__.py and tests only (R-155)", "R-203")),
        ("U05-infra-agents", heuristic_unused(("agent_civilization/agents/infrastructure/",
                                               "agent_civilization/agents/executors/",
                                               "agos_kernel/"), "R-203")),
        # ---- real, rebuilt in M10 -----------------------------------------------
        ("R01-web-ui", rx(r"^web/", "REDESIGN",
            "الواجهة المنشورة حقيقية لكنها تعرض «الوكلاء النشطون»=1000 ونسخًا خاملة، ولا إعدادات ولا موصلات ولا مفاتيح للمستخدم",
            "web/src/components/Dashboard.jsx: 'الوكلاء النشطون' = status.total_agents; Agents.jsx: "
            "'{agents.length} وكيل نشط'; live /api/agents returns 1000 rows, all status=idle", "R-201/R-233..R-237")),
        ("R02-server", rx(r"^server\.py$", "REDESIGN",
            "الخادم الحقيقي (FastAPI، ~3500 سطر في ملف واحد)؛ المحادثة توجّه بكلمات مفتاحية إلى معالج واحد",
            "ENTRY uvicorn server:app (Dockerfile, render.yaml); /api/chat -> routing/auto_router.route() "
            "keyword table -> one specialist", "R-202/R-224")),
        ("R03-civ-routes", rx(r"^agent_civilization/civilization_routes\.py$", "REDESIGN",
            "مخطط ومنفذ متعدد المراحل حقيقي مع موافقات، لكن الواجهة لا تستخدمه",
            "routes /civilization/plan|execute|runs|coordinate exist; web/src/api.js calls none of them", "R-224")),
        ("R04-router", rx(r"^agent_civilization/routing/", "REDESIGN",
            "توجيه بالكلمات المفتاحية؛ يحل محله المنسق",
            "routing/auto_router.py: 'Arabic + English intent keywords -> specializations'", "R-224")),
        ("R05-pipeline", rx(r"^agent_civilization/pipeline/", "REDESIGN",
            "تخطيط DAG حقيقي غير مستخدم من الواجهة؛ أساس المنسق", "imported by civilization_routes (R-155)", "R-224")),
        ("R06-specialist-core", rx(r"^agent_civilization/agents/specialists/(_templates/|[a-z_]+/__init__\.py$|__init__\.py$)",
            "REDESIGN", "المعالجات العشرون الحقيقية؛ تتحول إلى مواصفات وكلاء (دور، أدوات، حدود، تقييم)",
            "single base class per specialisation in _templates (R-105); dispatched by core/agents/real_dispatcher", "R-209/R-212")),
        ("R07-agent-model", rx(r"^agent_civilization/(core/agents/|core/agent_registry\.py$|agents/__init__\.py$|agents/external_adapter\.py$|agents/infrastructure/|agents/executors/|registry/__init__\.py$)",
            "REDESIGN", "نموذج الوكيل والسجل الحاليان؛ يُعاد بناؤهما على مواصفة الوكيل v1",
            "on the deploy-server import closure (R-155)", "R-209/R-210")),
        ("R08-llm", rx(r"^agent_civilization/core/llm\.py$", "REDESIGN",
            "استدعاء نموذج حقيقي (OpenRouter) بمفتاح واحد للمنصة؛ يصبح بوابة نماذج متعددة المزودين بمفاتيح المستخدم",
            "live /api/system/status: llm_provider=openrouter, llm_model=mistralai/mistral-nemo", "R-205/R-206")),
        ("R09-memory", rx(r"^(agent_civilization/core/experience\.py|agos_kernel/agent_production/)", "REDESIGN",
            "ذاكرة وتعلم حقيقيان على مسار التنفيذ لكن بلا ذاكرة مستخدم ولا قاعدة معرفة", "R-103/D-046 docstring", "R-215..R-218")),
        ("R10-connectors", rx(r"^agent_civilization/connectors/", "REDESIGN",
            "موصل GitHub حقيقي (R-191) غير مكشوف في الواجهة", "importers: tests/test_r191_connectors.py; dynamic handler_for('github')", "R-228..R-232")),
        ("R11-storage", rx(r"^agent_civilization/storage/", "REDESIGN",
            "تخزين Supabase حقيقي (مهام وأحداث)؛ يُضاف إليه المستخدمون والمساحات والمفاتيح", "live: supabase_configured=true, tasks_in_db>0", "R-204")),
        # ---- real and kept -------------------------------------------------------
        ("K01-real-agents", rx(r"^agent_civilization/agents/real/", "KEEP",
            "تنفيذ حقيقي للمعالجات (عقد التشغيل)", "on the deploy-server import closure (R-155)", "")),
        ("K02-core", rx(r"^(agent_civilization/|agos_kernel/infrastructure/)", "KEEP",
            "وحدة أساسية حقيقية على مسار التشغيل (أمان، مستأجرون، أدوات، قياس، طوابير، SSE...)",
            "R-155 reachability: {reach}", "")),
        ("K03-tests", rx(r"^(tests/|web/tests/|pytest\.ini$)", "KEEP",
            "اختبار؛ يتبع مصير ما يختبره عند تنفيذ R-203", "pytest / node test runner", "")),
        ("K04-governance", rx(r"^docs/governance/", "KEEP",
            "وثيقة حوكمة أو عقد أو خارطة طريق", "governance index docs/governance/README.md", "")),
        ("K05-audit", rx(r"^docs/(audit|sql)/", "KEEP",
            "سجل أدلة تاريخي أو مخطط قاعدة بيانات — لا يُحذف (D-064)", "evidence trail of R-001..R-199", "")),
        ("K06-benchmarks", rx(r"^benchmarks/", "KEEP",
            "قياسات مجمّدة مرجعية (R-169/R-176/R-177)", "referenced by evidence logs", "")),
        ("K07-scripts", rx(r"^scripts/", "KEEP",
            "أداة تشغيل أو تحقق أو بوابة حوكمة", "R-155 reachability: {reach}", "")),
        ("K08-ops", rx(r"^(\.github/|Dockerfile$|render\.yaml$|\.dockerignore$|\.env\.example$|\.gitignore$|requirements[\w.-]*$|run\.py$|README\.md$|SECURITY\.md$|DEPLOY\.md$|docs/)", "KEEP",
            "بنية تشغيل أو تبعيات أو وثيقة رئيسية", "R-155 entry: {entry}", "")),
    ]


def classify(base_rows: List[Dict[str, str]], git_show: Callable[[str], str]) -> List[Dict[str, str]]:
    shims = sorted(r["path"] for r in base_rows if SHIM_RE.match(r["path"]))
    ctx = {"shim_evidence": shim_evidence(shims, git_show) if shims else ""}
    rules = build_rules(ctx)
    out = []
    for b in base_rows:
        p = b["path"]
        verdict = reason = evidence = item = rule_id = ""
        for rid, fn in rules:
            hit = fn(p, b)
            if hit:
                verdict, reason, evidence, item = hit
                rule_id = rid
                break
        reach = b.get("entrypoint_or_imported_by", "")
        i = reach.find("reachable from")
        evidence = evidence.replace("{reach}", reach[i:i + 80] if i >= 0 else reach[:80]) \
                           .replace("{entry}", reach[:80])
        out.append({"path": p, "area": b.get("owner_component", ""), "verdict": verdict,
                    "verdict_ar": VERDICT_AR.get(verdict, ""), "rule": rule_id or "NONE",
                    "reason": reason, "evidence": evidence, "m10_item": item,
                    "base_status": b.get("inventory_status", ""),
                    "base_decision": b.get("decision_no_delete", "")})
    return out


def self_check(rows: List[Dict[str, str]]) -> List[str]:
    errs = []
    paths = [r["path"] for r in rows]
    if len(paths) != len(set(paths)):
        errs.append("duplicate paths")
    for r in rows:
        if r["verdict"] not in VERDICTS:
            errs.append(f"{r['path']}: no verdict")
        if r["verdict"].endswith("_REMOVE") and not (r["reason"].strip() and r["evidence"].strip()):
            errs.append(f"{r['path']}: {r['verdict']} without reason/evidence")
    return errs


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("base_csv")
    ap.add_argument("out_csv")
    ap.add_argument("--summary")
    ap.add_argument("--repo", default=".")
    ap.add_argument("--sha", default="HEAD")
    a = ap.parse_args(argv)
    with open(a.base_csv, encoding="utf-8", newline="") as f:
        base = list(csv.DictReader(f))

    def git_show(path: str) -> str:
        return subprocess.run(["git", "-C", a.repo, "show", f"{a.sha}:{path}"], check=True,
                              capture_output=True, text=True).stdout

    rows = classify(base, git_show)
    with open(a.out_csv, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    errs = self_check(rows)
    summary = {"rows": len(rows), "verdicts": dict(Counter(r["verdict"] for r in rows)),
               "rules": dict(sorted(Counter(r["rule"] for r in rows).items())),
               "self_check": "PASS" if not errs else "FAIL", "errors": errs[:50]}
    if a.summary:
        with open(a.summary, "w", encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=1, sort_keys=True)
            f.write("\n")
    print(json.dumps({k: summary[k] for k in ("rows", "verdicts", "self_check")}, ensure_ascii=False))
    return 0 if not errs else 1


if __name__ == "__main__":
    sys.exit(main())
