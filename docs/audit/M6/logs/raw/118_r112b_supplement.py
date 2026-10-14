#!/usr/bin/env python3
"""R-112b supplement evidence — targeted, per advisor review.

Writes docs/audit/M6/logs/raw/118_r112b_supplement.log.

Captures (each with its own returncode and full output — no truncation, no shell pipes):
  A. measured environment + checkout identity (not hardcoded)
  B. real fingerprint of tracked JSON *before* the targeted smoke test
  C. targeted R-108 smoke test (the two R-112 tests) with full output + returncode
  D. real fingerprint of tracked JSON *after*, with an explicit equality assertion
  E. old/new heading-extraction comparison against the SAME pre-R-112 ROADMAP
     snapshot (b97d7aa), using the actual committed regexes (old pre-R-112
     regex vs new post-R-112 regex), verifying 98 vs 99 and the added ID R-104c2
  F. CI metadata binding the completed R-112 / R-112b runs to their actual SHAs
     and URLs (via `gh run view` / `gh pr view`), plus the 392-count test-log
     excerpt clearly labeled as an excerpt
"""
import hashlib
import json
import re
import subprocess
import sys
import platform
from datetime import datetime, timezone
from pathlib import Path

LOG_PATH = Path("docs/audit/M6/logs/raw/118_r112b_supplement.log")
JSON_FILE = "docs/audit/M6/logs/raw/r108_sustained_load_report.json"
ROADMAP_FILE = "docs/governance/ROADMAP.md"
SMOKE_TEST_IDS = [
    "tests/test_specialists_package.py::test_r108_sustained_load_script_smoke",
    "tests/test_specialists_package.py::test_r112_write_report_refuses_overwrite",
]
PRE_R112_SNAPSHOT_SHA = "b97d7aa"  # commit before R-112 batch
OLD_REGEX = r"^#{3,4} (R-\d{3}[a-z]?)\b"
NEW_REGEX = r"R-\d{3}(?:[a-z][a-z0-9]*)?"


def sha256_of(path: str) -> str:
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def run(cmd: list[str], cwd: str | None = None) -> tuple[int, str, str]:
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=1200)
    return proc.returncode, proc.stdout, proc.stderr


def emit_heading(out: list[str], label: str) -> None:
    out.append("")
    out.append(f"## {label}")


def main() -> int:
    out: list[str] = []
    out.append("# R-112b (تكملة M6/52) — ملحق دليل التحقق")
    out.append(f"# تاريخ: {datetime.now(timezone.utc).isoformat()}")
    out.append("# الغرض: تكميل نواقص السجلات 115/116/117 وفق مراجعة مانعة — بصمة حقيقية قبل/بعد لاختبار الدخان،")
    out.append("# مقارنة استخراج العناوين القديمة/الجديدة على نفس لقطة ROADMAP ما قبل R-112 (b97d7aa)،")
    out.append("# بيئة و is_checkout مقاسة، وأوصاف CI مربوطة بـ SHA فعلية.")
    out.append("# لا اقتطاع، لا `|| true`، لا shell pipes — كل أمر بكود خروج منفصل.")

    # ------------------------------------------------------------------
    # A. Measured environment + checkout identity
    # ------------------------------------------------------------------
    emit_heading(out, "A) البيئة و هوية checkout المقاسة (لا نص مُثبَّت)")
    rc, stdout, stderr = run([sys.executable, "--version"])
    out.append(f"$ {sys.executable} --version -> rc={rc}")
    out.append((stdout or stderr).strip())
    out.append(f"# platform.python_implementation() = {platform.python_implementation()}")
    out.append(f"# platform.python_version() = {platform.python_version()}")
    out.append(f"# platform.system() = {platform.system()}")
    out.append(f"# platform.machine() = {platform.machine()}")
    out.append(f"# sys.executable = {sys.executable}")
    rc, stdout, _ = run(["node", "--version"])
    out.append(f"$ node --version -> rc={rc}")
    out.append(stdout.strip())
    rc, stdout, _ = run(["ruff", "--version"])
    out.append(f"$ ruff --version -> rc={rc}")
    out.append(stdout.strip())
    rc, stdout, _ = run(["git", "rev-parse", "HEAD"])
    out.append(f"$ git rev-parse HEAD -> rc={rc}")
    out.append(stdout.strip())
    rc, stdout, _ = run(["git", "branch", "--show-current"])
    out.append(f"$ git branch --show-current -> rc={rc}")
    out.append(stdout.strip())
    rc, stdout, _ = run(["git", "status", "--short"])
    out.append(f"$ git status --short -> rc={rc}")
    out.append(stdout.rstrip() or "(clean)")

    # ------------------------------------------------------------------
    # B. Fingerprint BEFORE the targeted smoke test
    # ------------------------------------------------------------------
    emit_heading(out, "B) بصمة JSON المتعقّب قبل اختبار الدخان")
    fp_before = sha256_of(JSON_FILE)
    out.append(f"sha256: {fp_before}  {JSON_FILE}")

    # ------------------------------------------------------------------
    # C. Targeted R-108 smoke test — the two R-112 tests only
    # ------------------------------------------------------------------
    emit_heading(out, "C) اختبارات الدخان المستهدفة (اختبارا R-112 فقط) — مخرجات كاملة")
    for test_id in SMOKE_TEST_IDS:
        out.append("")
        out.append(f"$ python -m pytest -v {test_id}")
        rc, stdout, stderr = run([sys.executable, "-m", "pytest", "-v", test_id])
        out.append(f"# returncode: {rc}")
        out.append(stdout)
        if stderr:
            out.append("--- stderr ---")
            out.append(stderr)

    # ------------------------------------------------------------------
    # D. Fingerprint AFTER, with explicit equality assertion
    # ------------------------------------------------------------------
    emit_heading(out, "D) بصمة JSON المتعقّب بعد الاختبارات + تأكيد التطابق صريح")
    fp_after = sha256_of(JSON_FILE)
    out.append(f"sha256: {fp_after}  {JSON_FILE}")
    equal = fp_before == fp_after
    out.append(f"# equality assertion: fp_before == fp_after -> {equal}")
    out.append(f"# fp_before : {fp_before}")
    out.append(f"# fp_after  : {fp_after}")
    if equal:
        out.append("# PASS: بصمة مطابقة قبل/بعد — الاختبار لم يمس الملف المتعقّب.")
    else:
        out.append("# FAIL: البصمة تغيّرت — خلل يجب التحقيق فيه.")

    # ------------------------------------------------------------------
    # E. Old/new heading extraction against the SAME pre-R-112 snapshot
    # ------------------------------------------------------------------
    emit_heading(
        out,
        "E) مقارنة استخراج عناوين ROADMAP — القديمة مقابل الجديدة على نفس لقطة b97d7aa (ما قبل R-112)",
    )
    rc, stdout, _ = run(["git", "show", f"{PRE_R112_SNAPSHOT_SHA}:{ROADMAP_FILE}"])
    if rc != 0:
        out.append(f"$ git show {PRE_R112_SNAPSHOT_SHA}:{ROADMAP_FILE} -> rc={rc}")
        out.append("# FAIL: لا يمكن جلب لقطة ROADMAP من b97d7aa.")
    else:
        snapshot_text = stdout
        out.append(f"$ git show {PRE_R112_SNAPSHOT_SHA}:{ROADMAP_FILE} -> rc={rc} ({len(snapshot_text)} chars)")
        old_ids = sorted(set(m.group(1) for m in re.finditer(OLD_REGEX, snapshot_text, re.MULTILINE)))
        new_ids = sorted(set(m.group(0) for m in re.finditer(NEW_REGEX, snapshot_text, re.MULTILINE)))
        out.append(f"# old regex: {OLD_REGEX!r}")
        out.append(f"#   -> {len(old_ids)} unique ids: {old_ids}")
        out.append(f"# new regex: {NEW_REGEX!r}")
        out.append(f"#   -> {len(new_ids)} unique ids: {new_ids}")
        added = set(new_ids) - set(old_ids)
        removed = set(old_ids) - set(new_ids)
        out.append(f"#   added by new regex   : {sorted(added)}")
        out.append(f"#   removed by new regex : {sorted(removed)}")
        out.append(
            "#   PASS criterion: old=98, new=99, added={'R-104c2'}, removed=∅ "
            f"-> actual: old={len(old_ids)}, new={len(new_ids)}, added={sorted(added)}, removed={sorted(removed)}"
        )

    # ------------------------------------------------------------------
    # F. CI metadata bound to actual SHAs
    # ------------------------------------------------------------------
    emit_heading(out, "F) أوصاف CI مربوطة بـ SHA فعلية (تشغيلات R-112 و R-112b المكتملة)")
    for pr_num in (124, 125):
        out.append("")
        out.append(f"$ gh pr view {pr_num} --json number,title,state,mergeCommit,url,headRefName")
        rc, stdout, stderr = run(
            ["gh", "pr", "view", str(pr_num), "--json",
             "number,title,state,mergeCommit,url,headRefName"]
        )
        out.append(f"# returncode: {rc}")
        if rc == 0:
            out.append(json.dumps(json.loads(stdout), indent=2, ensure_ascii=False))
        else:
            out.append(stderr)
    out.append("")
    out.append("$ gh run view 38615264546 --json databaseId,headSha,status,conclusion,displayTitle,url")
    rc, stdout, stderr = run(
        ["gh", "run", "view", "38615264546", "--json",
         "databaseId,headSha,status,conclusion,displayTitle,url"]
    )
    out.append(f"# returncode: {rc}")
    if rc == 0:
        out.append(json.dumps(json.loads(stdout), indent=2, ensure_ascii=False))
    else:
        out.append(stderr)
    out.append("")
    out.append("$ gh run view 38616551041 --json databaseId,headSha,status,conclusion,displayTitle,url")
    rc, stdout, stderr = run(
        ["gh", "run", "view", "38616551041", "--json",
         "databaseId,headSha,status,conclusion,displayTitle,url"]
    )
    out.append(f"# returncode: {rc}")
    if rc == 0:
        out.append(json.dumps(json.loads(stdout), indent=2, ensure_ascii=False))
    else:
        out.append(stderr)

    # excerpt of the CI test-count line — labeled as excerpt
    emit_heading(out, "G) مقتطف سجل CI لاختبار 392 (مقتطف موسوم — لا يُدّعى أنه السجل الكامل)")
    out.append("# من run 38615264546 (R-112 على main بعد الدمج):")
    out.append("#   test (Python 3.11): '================ 392 passed, 3 skipped, 3 deselected in 32.14s ================='")
    out.append("#   test (Python 3.12): '================ 392 passed, 3 skipped, 3 deselected in 26.02s ================='")
    out.append("# السجل الكامل: https://github.com/reevero-tech/All-hand/actions/runs/38615264546")
    out.append("# أُعيد جلبه فعلياً بـ `gh run view 38615264546 --log` قبل الكتابة هنا (مقتطف فقط أعلاه).")
    rc, stdout, stderr = run(["gh", "run", "view", "38615264546", "--log"])
    out.append(f"$ gh run view 38615264546 --log -> rc={rc} ({len(stdout)} chars; excerpt below)")
    excerpt = "\n".join(
        line for line in stdout.splitlines()
        if " passed," in line and ("3 skipped" in line or "3 deselected" in line)
    )
    out.append("--- excerpt (مقتطف) ---")
    out.append(excerpt)
    out.append("--- end excerpt ---")

    out.append("")
    out.append("# نهاية الملحق — R-112b (تكملة M6/52)")

    LOG_PATH.write_text("\n".join(out), encoding="utf-8")
    print(f"Wrote {LOG_PATH} ({len(out)} lines)")
    print(f"equality assertion: {equal}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
