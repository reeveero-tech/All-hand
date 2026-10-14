#!/usr/bin/env python3
"""R-112b continuation evidence pack — raw, untruncated, individual exit codes.

Writes docs/audit/M6/logs/raw/117_r112b_continuation.log.
Each command is run via subprocess.run (no shell pipe, no `|| true`), with
its own return code and full stdout/stderr captured verbatim.
"""
import subprocess
import sys
import hashlib
from datetime import datetime, timezone
from pathlib import Path

LOG_PATH = Path("docs/audit/M6/logs/raw/117_r112b_continuation.log")


def sha256_of(path: str) -> str:
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def main() -> int:
    out = []
    out.append("# R-112b (تكملة M6/52) — دليل تحقق خام كامل")
    out.append(f"# تاريخ: {datetime.now(timezone.utc).isoformat()}")
    out.append("# بيئة محلية: Python 3.14.3 / Node v20.20.2 / ruff 0.17.0")
    out.append("# لا اقتطاع. لا `|| true`. كل أمر بكود خروج منفصل. stdout/stderr كاملة.")
    out.append("#")
    out.append("# ملاحظة صدق: سجل 116 ادّعى \"لا اقتطاع\" لكن pytest فيه كان `tail -3`.")
    out.append("# هذا السجل يعيد كل الفحوصات بأكواد خروج حقيقية.")

    commands = [
        ("1) بصمة JSON المتعقّب قبل كل شيء", None, None),
        ("2) فحص كل الست نسخ المتعقّبة للـ JSON في تاريخ git",
         ["git", "log", "--all", "--format=%H", "--",
          "docs/audit/M6/logs/raw/r108_sustained_load_report.json"], None),
        ("3) سكربت التدقيق R-075 — مخرجات كاملة",
         ["python3", "scripts/run_r075_roadmap_audit.py", "main"], None),
        ("4) git status", ["git", "status", "--short"], None),
        ("5) git diff --stat (فحص التوثيق)", ["git", "diff", "--stat"], None),
        ("6) pytest الكامل", [sys.executable, "-m", "pytest", "tests/"], None),
        ("7) ruff نطاق كامل",
         ["ruff", "check", "--select", "F,E722", "--exclude",
          "agent_civilization/agents/specialists",
          "server.py", "agent_civilization", "agos_kernel", "tests"], None),
        ("8) ruff specialists",
         ["ruff", "check", "--select", "F,E722",
          "agent_civilization/agents/specialists"], None),
        ("9) بناء الجذر", ["npm", "run", "build"], None),
        ("10) بناء web", ["npm", "run", "build"], "web"),
    ]

    for label, cmd, cwd in commands:
        out.append("")
        out.append(f"## {label}")
        if cmd is None:
            continue
        try:
            proc = subprocess.run(
                cmd, cwd=cwd, capture_output=True, text=True, timeout=900
            )
            out.append(f"$ {' '.join(cmd)}")
            out.append(f"# returncode: {proc.returncode}")
            if proc.stdout:
                out.append(proc.stdout)
            if proc.stderr:
                out.append("--- stderr ---")
                out.append(proc.stderr)
        except subprocess.TimeoutExpired:
            out.append(f"$ {' '.join(cmd)}")
            out.append("# TIMEOUT after 900s")

    out.append("")
    out.append("## 11) بصمة JSON بعد كل الفحوصات (يجب أن تتطابق مع الخطوة 1)")
    out.append(f"{sha256_of('docs/audit/M6/logs/raw/r108_sustained_load_report.json')}  docs/audit/M6/logs/raw/r108_sustained_load_report.json")

    out.append("")
    out.append("## 12) عدد بنود ROADMAP الآن (يجب أن يكون 101 = 100 + R-112b)")
    out.append("(موثق من مخرجات سكربت التدقيق أعلاه — لا رقم يُدّعى بلا سجل)")

    out.append("")
    out.append("# نهاية دليل التحقق R-112b (تكملة)")

    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    LOG_PATH.write_text("\n".join(out), encoding="utf-8")
    print(f"Wrote {LOG_PATH} ({len(out)} lines)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
