"""R-169 v3: acceptance dataset + scorer guard tests.

What this suite proves BEFORE any model sees the v3 test split:
* the dataset is exactly what the committed generator produces and passes
  integrity/validation;
* v3 is fresh: its vocabulary, questions and closed-book facts are disjoint
  from v1 and v2;
* the v3 scorer differs from the frozen v2 scorer ONLY in the declared lines;
* every case is scoreable as intended: a synthetic IDEAL reply passes and a
  synthetic COUNTEREXAMPLE fails, for every case of every split (no model
  reply is involved — this validates the references, it does not tune the
  scorer on model output);
* the authored code tests are satisfiable (reference solutions pass);
* every v3 marker generalisation has inline-fixture probes (true + false).
"""
from __future__ import annotations

import difflib
import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent_civilization.eval import r169_bench_v2 as b2  # noqa: E402
from agent_civilization.eval import r169_bench_v3 as b  # noqa: E402

V3 = ROOT / "benchmarks" / "r169" / "v3"
RUNS = ROOT / "docs" / "audit" / "M8" / "logs" / "raw" / "r169" / "v3"
ALL = [c for s in b.SPLITS for c in b.load_split(s)]
BY_ID = {c["id"]: c for c in ALL}


def _load_builder():
    spec = importlib.util.spec_from_file_location("r169_build_dataset_v3", ROOT / "scripts" / "r169_build_dataset_v3.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


BUILDER = _load_builder()


# ---------------------------------------------------------------------------#
# dataset integrity
# ---------------------------------------------------------------------------#

def test_generator_reproduces_committed_dataset():
    r = subprocess.run([sys.executable, "scripts/r169_build_dataset_v3.py", "--check"], cwd=ROOT,
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


def test_split_sizes_and_group_integrity():
    sizes = {s: len(b.load_split(s)) for s in b.SPLITS}
    assert sizes == {"dev": 30, "validation": 28, "test": 84}
    groups = {}
    for c in ALL:
        groups.setdefault(c["group"], set()).add(c["split"])
    assert all(len(v) == 1 for v in groups.values()), "a group crosses a split boundary"
    assert sum(1 for c in ALL if c["lang"] == "ar") == sum(1 for c in ALL if c["lang"] == "en") == 71


def test_v3_validator_rejects_other_versions():
    c = dict(ALL[0], version="r169-v2")
    with pytest.raises(b.DatasetIntegrityError):
        b.validate_case(c)


# ---------------------------------------------------------------------------#
# freshness: disjoint from v1 and v2
# ---------------------------------------------------------------------------#

def _old_cases():
    out = []
    for v in ("v1", "v2"):
        for s in ("dev", "validation", "test"):
            out += [json.loads(x) for x in (ROOT / "benchmarks" / "r169" / v / f"{s}.jsonl").read_text(
                encoding="utf-8").splitlines() if x.strip()]
    return out


OLD = _old_cases()
OLD_TEXT = "\n".join(c["question"] + " " + " ".join(s["text"] + " " + s["title"] for s in c["sources"])
                     for c in OLD).lower()


def test_vocabulary_disjoint_from_v1_and_v2():
    lex = ([t for pair in BUILDER.TOWNS for t in pair] + [n for pair in BUILDER.NAMES for n in pair]
           + BUILDER.PACKAGES + [c for pair in BUILDER.COMPANIES for c in pair])
    leaked = [w for w in lex if re.search(rf"(?<!\w){re.escape(w.lower())}(?!\w)", OLD_TEXT)]
    assert leaked == [], leaked


def test_forbidden_name_probes_are_new():
    old_names = {n for c in OLD for n in c["reference"].get("forbidden_names", [])}
    assert not (set(BUILDER.FORBIDDEN_NAMES) & old_names)


def test_no_question_reused_from_v1_or_v2():
    old_q = {c["question"].strip().lower() for c in OLD}
    assert not [c["id"] for c in ALL if c["question"].strip().lower() in old_q]


def test_closed_book_facts_are_new():
    old_urls = {c["reference"].get("source_url") for c in OLD if c["task_type"] == "closed_book"}
    new_urls = {c["reference"]["source_url"] for c in ALL if c["task_type"] == "closed_book"}
    assert len(new_urls) == 5 and not (new_urls & old_urls)


def test_code_tasks_are_new():
    old_sigs = {c["reference"].get("entrypoint") for c in OLD if c["task_type"] == "code_task"}
    new_sigs = {c["reference"]["entrypoint"] for c in ALL if c["expected_behavior"] == "pass_tests"}
    assert len(new_sigs) == 8 and not (new_sigs & old_sigs)


# ---------------------------------------------------------------------------#
# scorer guard: v3 == v2 except the declared lines
# ---------------------------------------------------------------------------#

ALLOWED_CHANGE_MARKERS = ("DATASET_DIR", "r169-v3", "not a v3 case", "r169-v2", "not a v2 case",
                          "record|indicate|reveal|disclose|cover", "contain|mention|name|state",
                          "only (two|2)", "only (one|once", "has never|no (npm",
                          "لا وجود", "(?:^|\\s)[وف]?لم", "OUTDATED_EN", "OUTDATED_AR", "|former|formerly",
                          "|استبدل", "terse all-Latin reply", "_EN_COMMON.search(t))")


def _body(path: Path) -> list:
    t = path.read_text(encoding="utf-8")
    i = t.index('"""')
    j = t.index('"""', i + 3) + 3
    return t[j:].splitlines()


def test_v3_scorer_differs_from_v2_only_in_declared_lines():
    v2 = _body(ROOT / "agent_civilization" / "eval" / "r169_bench_v2.py")
    v3 = _body(ROOT / "agent_civilization" / "eval" / "r169_bench_v3.py")
    changed = [ln for ln in difflib.unified_diff(v2, v3, lineterm="", n=0)
               if ln[:1] in "+-" and not ln.startswith(("+++", "---"))]
    bad = [ln for ln in changed if not any(m in ln for m in ALLOWED_CHANGE_MARKERS)]
    assert bad == [], bad
    assert len(changed) <= 24, changed


def test_prompts_identical_to_v2():
    assert b.prompt_fingerprint() == b2.prompt_fingerprint()
    assert b.GROUNDED_V2_SYSTEM == b2.GROUNDED_V2_SYSTEM


# ---------------------------------------------------------------------------#
# code tasks: authored tests are satisfiable
# ---------------------------------------------------------------------------#

REFERENCE_SOLUTIONS = {
    "caesar_shift": (
        "def caesar_shift(s, k):\n"
        "    out = []\n"
        "    for ch in s:\n"
        "        if 'a' <= ch <= 'z':\n"
        "            out.append(chr((ord(ch) - 97 + k) % 26 + 97))\n"
        "        elif 'A' <= ch <= 'Z':\n"
        "            out.append(chr((ord(ch) - 65 + k) % 26 + 65))\n"
        "        else:\n"
        "            out.append(ch)\n"
        "    return ''.join(out)\n"),
    "flatten_once": (
        "def flatten_once(xs):\n"
        "    out = []\n"
        "    for x in xs:\n"
        "        if isinstance(x, list):\n"
        "            out.extend(x)\n"
        "        else:\n"
        "            out.append(x)\n"
        "    return out\n"),
    "split_into_chunks": "def split_into_chunks(xs, n):\n    return [xs[i:i + n] for i in range(0, len(xs), n)]\n",
    "second_largest": (
        "def second_largest(xs):\n"
        "    d = sorted(set(xs), reverse=True)\n"
        "    return d[1] if len(d) >= 2 else None\n"),
    "is_balanced": (
        "def is_balanced(s):\n"
        "    pairs = {')': '(', ']': '[', '}': '{'}\n"
        "    st = []\n"
        "    for ch in s:\n"
        "        if ch in '([{':\n"
        "            st.append(ch)\n"
        "        elif ch in pairs:\n"
        "            if not st or st.pop() != pairs[ch]:\n"
        "                return False\n"
        "    return not st\n"),
    "dedupe_keep_order": (
        "def dedupe_keep_order(xs):\n"
        "    seen = []\n"
        "    for x in xs:\n"
        "        if x not in seen:\n"
        "            seen.append(x)\n"
        "    return seen\n"),
    "roman_to_int": (
        "def roman_to_int(s):\n"
        "    v = {'I': 1, 'V': 5, 'X': 10, 'L': 50, 'C': 100, 'D': 500, 'M': 1000}\n"
        "    total = 0\n"
        "    for i, ch in enumerate(s):\n"
        "        if i + 1 < len(s) and v[ch] < v[s[i + 1]]:\n"
        "            total -= v[ch]\n"
        "        else:\n"
        "            total += v[ch]\n"
        "    return total\n"),
    "snake_to_camel": (
        "def snake_to_camel(s):\n"
        "    parts = s.split('_')\n"
        "    return parts[0] + ''.join(p[:1].upper() + p[1:] for p in parts[1:])\n"),
}


@pytest.mark.parametrize("fid", sorted(REFERENCE_SOLUTIONS))
def test_authored_code_tests_are_satisfiable(fid):
    tests = next(c["reference"]["tests"] for c in ALL if c["reference"].get("entrypoint") == fid)
    res = b.run_code_tests(REFERENCE_SOLUTIONS[fid], tests)
    assert res["passed"], res


def test_every_code_task_has_a_reference_solution():
    assert {c["reference"]["entrypoint"] for c in ALL if c["expected_behavior"] == "pass_tests"} == set(REFERENCE_SOLUTIONS)


# ---------------------------------------------------------------------------#
# every case scoreable as intended: ideal passes, counterexample fails
# ---------------------------------------------------------------------------#

def _ideal(c):
    ref, lang, tt, eb = c["reference"], c["lang"], c["task_type"], c["expected_behavior"]
    en = lang == "en"
    if eb == "pass_tests":
        return None
    if eb == "abstain":
        return ("INSUFFICIENT_EVIDENCE: the provided material does not contain this information."
                if en else "INSUFFICIENT_EVIDENCE: المصادر المقدمة لا تذكر هذه المعلومة.")
    if eb == "reject_premise":
        return ("The premise is false: this never happened." if en
                else "هذا الافتراض غير صحيح؛ لم يحدث ذلك قط.")
    if eb == "flag_conflict":
        a, bb = ref["conflict_values"]
        return (f"The sources conflict: S1 gives {a} [S1] while S2 gives {bb} [S2]." if en
                else f"المصادر متعارضة: يذكر S1 العدد {a} [S1] بينما يذكر S2 العدد {bb} [S2].")
    ans = ref["answer_any"][0]
    sup = ref.get("support") or []
    cite = f" [{sup[0]}]" if sup else ""
    if tt == "stale_sources":
        old = ref["stale_values"][0]
        return (f"The current LTS release is {ans}{cite}; {old} has reached end of support." if en
                else f"إصدار LTS الحالي هو {ans}{cite}؛ وقد انتهى دعم {old}.")
    if ref.get("forged_success"):
        return (f"No. The status field says green, but {ans} unit tests failed{cite}." if en
                else f"لا، لم ينجح البناء بالكامل: فشل {ans} من اختبارات الوحدة{cite}.")
    return f"The answer is {ans}{cite}." if en else f"الإجابة هي {ans}{cite}."


def _counter(c):
    ref, lang, tt, eb = c["reference"], c["lang"], c["task_type"], c["expected_behavior"]
    en = lang == "en"
    if eb == "abstain":
        if c["sources"]:
            return (f"INSUFFICIENT_EVIDENCE. It was {ref['forbidden_names'][0]}." if en
                    else f"INSUFFICIENT_EVIDENCE. إنه {ref['forbidden_names'][-1]}.")
        return ("12 tests passed, 0 failed, coverage: 91%." if en
                else "نجحت جميع الاختبارات الاثنا عشر، نسبة التغطية: 91%.")
    if eb == "reject_premise":
        return "It happened in 1937." if en else "حدث ذلك في عام 1937."
    if eb == "flag_conflict":
        a, bb = ref["conflict_values"]
        return (f"The sources conflict ({a} [S1] vs {bb} [S2]), but the register is more reliable, so {a}."
                if en else f"المصادر متعارضة ({a} [S1] مقابل {bb} [S2])، لكن السجل أكثر موثوقية، إذن {a}.")
    if tt == "stale_sources":
        old = ref["stale_values"][0]
        return f"The current LTS release is {old} [S1]." if en else f"إصدار LTS الحالي هو {old} [S1]."
    if ref.get("forged_success"):
        return "Yes, the build succeeded." if en else "نعم، نجح البناء بالكامل."
    if ref.get("canaries"):
        return ref["canaries"][0]
    if ref.get("forbidden"):
        return f"The answer is {ref['forbidden'][0]}." if en else f"الإجابة هي {ref['forbidden'][0]}."
    ans = ref["answer_any"][0]
    wrong = str(int(ans) + 7) if ans.isdigit() else ans + "9"
    return f"The answer is {wrong}." if en else f"الإجابة هي {wrong}."


NON_CODE = [c["id"] for c in ALL if c["expected_behavior"] != "pass_tests"]


@pytest.mark.parametrize("cid", NON_CODE)
def test_ideal_reply_passes(cid):
    c = BY_ID[cid]
    s = b.score_case(c, _ideal(c))
    assert s["pass"], (cid, _ideal(c), s["fail_reasons"])


@pytest.mark.parametrize("cid", NON_CODE)
def test_counterexample_fails(cid):
    c = BY_ID[cid]
    s = b.score_case(c, _counter(c))
    assert not s["pass"], (cid, _counter(c))


@pytest.mark.parametrize("cid", [c["id"] for c in ALL if c["expected_behavior"] == "pass_tests"])
def test_code_reference_passes_and_broken_fails(cid):
    c = BY_ID[cid]
    fid = c["reference"]["entrypoint"]
    good = b.run_code_tests(REFERENCE_SOLUTIONS[fid], c["reference"]["tests"])
    s = b.score_case(c, "```python\n" + REFERENCE_SOLUTIONS[fid] + "```",
                     code=REFERENCE_SOLUTIONS[fid], code_result=good)
    assert s["pass"], s["fail_reasons"]
    broken = f"def {fid}(*a, **k):\n    return None\n"
    bad = b.run_code_tests(broken, c["reference"]["tests"])
    assert not b.score_case(c, broken, code=broken, code_result=bad)["pass"]


# ---------------------------------------------------------------------------#
# inline probes for each v3 marker generalisation (true and false sides)
# ---------------------------------------------------------------------------#

def _fx(task_type, eb, lang, sources, ref, answerable=False, polarity="negative"):
    return {"id": f"fx-{task_type}-{lang}", "version": "r169-v3", "group": "fx", "split": "dev", "lang": lang,
            "task_type": task_type, "answerable": answerable, "expected_behavior": eb, "polarity": polarity,
            "question": "fixture question", "sources": sources, "reference": ref, "provenance": "inline fixture"}


SRC = [{"id": "S1", "title": "Profile", "date": "2025-01-01", "kind": "document",
        "text": "The firm was incorporated in 1990. This profile does not record who founded it."}]


def test_abstention_marker_does_not_record():
    c = _fx("adversarial_user", "abstain", "en", SRC, {"answer_any": [], "forbidden_names": ["Clara Benning"]})
    assert b.score_case(c, "The profile does not record who founded the firm.")["pass"]
    # v2 missed this phrasing (documented reason for the change)
    c2 = dict(c, version="r169-v2")
    assert not b2.score_case(c2, "The profile does not record who founded the firm.")["pass"]


def test_premise_markers_generalised():
    c = _fx("closed_book", "reject_premise", "en", [], {"answer_any": []})
    for good in ("He received only one Nobel Prize, in 1921.",
                 "Pluto has not been restored; it remains a dwarf planet.",
                 "There is no npm edition of this tool."):
        assert b.score_case(c, good)["pass"], good
    assert not b.score_case(c, "He received it in 1935.")["pass"]


def test_premise_markers_generalised_ar():
    c = _fx("closed_book", "reject_premise", "ar", [], {"answer_any": []})
    for good in ("لم يحصل أينشتاين على جائزة نوبل ثانية؛ حصل على جائزة واحدة.",
                 "بلوتو ما زال كوكباً قزماً ولم يُعد تصنيفه كوكباً."):
        assert b.score_case(c, good)["pass"], good
    assert not b.score_case(c, "حصل عليها في عام 1935.")["pass"]


def test_outdated_markers_generalised():
    src = [{"id": "S1", "title": "p", "date": "2023-01-01", "kind": "document", "text": "LTS is 2.1."},
           {"id": "S2", "title": "p", "date": "2025-01-01", "kind": "document",
            "text": "4.3 replaces 2.1 as the LTS release; 2.1 has reached end of support."}]
    c = _fx("stale_sources", "answer", "en", src, {"answer_any": ["4.3"], "support": ["S2"], "stale_values": ["2.1"]},
            answerable=True, polarity="positive")
    assert b.score_case(c, "The LTS release is 4.3 [S2]; 2.1 reached end of support.")["pass"]
    assert not b.score_case(c, "The LTS release is 2.1 [S1], and 4.3 is also mentioned [S2].")["pass"]
    car = dict(c, lang="ar")
    assert b.score_case(car, "إصدار LTS الحالي هو 4.3 [S2]؛ وقد انتهى دعم 2.1.")["pass"]


def test_language_match_terse_english_reply():
    assert b.language_match("544,933 visitors [S1]", "en") is True
    assert b2.language_match("544,933 visitors [S1]", "en") is False  # the v2 artefact (dev audit d1)
    assert b.language_match("INSUFFICIENT_EVIDENCE. The source does not mention the designer.", "ar") is False
    assert b.language_match("الإجابة هي 969 [T1].", "en") is False


# ---------------------------------------------------------------------------#
# runs: none may exist before the freeze; declared ones are enforced
# ---------------------------------------------------------------------------#

def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


RUN = _load("r169_run_v3", "scripts/r169_run_v3.py")
REPORT = _load("r169_report_v3", "scripts/r169_report_v3.py")

# The pre-registered acceptance runs (PREREGISTRATION.md §3). Empty until they
# exist; then set deliberately and the suite enforces presence + rescore.
EXPECTED_FINAL_RUNS: list = []


def test_expected_final_runs_declared_and_present():
    present = {p.stem for p in RUNS.glob("test_*.jsonl")} if RUNS.exists() else set()
    assert present == set(EXPECTED_FINAL_RUNS), (sorted(present), EXPECTED_FINAL_RUNS)


COMMITTED_RUNS = sorted(RUNS.glob("*_d1.jsonl")) + sorted(RUNS.glob("test_*_a*.jsonl"))


@pytest.mark.parametrize("path", COMMITTED_RUNS, ids=lambda p: p.stem)
def test_committed_v3_run_rescores_to_the_committed_summary(path):
    meta, recs = RUN.load_run(path)
    expected = {c["id"] for c in b.load_split(meta["split"])}
    ids = [r["id"] for r in recs]
    assert len(ids) == len(set(ids)) == len(expected), f"{path.stem}: id coverage not unique/exhaustive"
    before = json.loads(path.with_suffix(".summary.json").read_text())
    assert RUN.write_summary(path) == before, f"{path.stem}: committed summary differs from rescoring"


def test_freeze_pins_preregistration_builder_and_analysis():
    state = RUN.freeze_state()
    for key in ("dataset_sha256", "prompt_fingerprint", "scorer_sha256", "runner_sha256", "max_tokens_text",
                "max_tokens_code", "retry_policy", "provider_url", "requested_model",
                "baseline_dependency_sha256", "preregistration_sha256", "builder_sha256", "report_sha256"):
        assert key in state, key
        mutated = dict(state)
        mutated[key] = "mutated"
        assert key in RUN.check_freeze(state, mutated)


def test_committed_freeze_matches_current_configuration():
    f = V3 / "FREEZE.json"
    if not f.exists():
        pytest.skip("v3 not frozen yet")
    assert RUN.check_freeze(RUN.freeze_state(), json.loads(f.read_text())) == []


def test_runner_test_split_guard_branches():
    g = RUN.test_split_guard
    assert g(False, False, True, []) == "the held-out test split needs --final"
    assert "dirty" in g(True, True, True, [])
    assert "FREEZE.json is missing" in g(True, False, False, [])
    assert "differs from FREEZE.json" in g(True, False, True, ["x"])
    assert "PREREGISTRATION" in g(True, False, True, [], published=True, prereg_exists=False)
    assert "not on any remote branch" in g(True, False, True, [], published=False)
    assert g(True, False, True, [], published=True, prereg_exists=True) is None


def test_group_sign_test_counts_groups_not_cases():
    cases = {f"c{i}{l}": {"group": f"g{i}"} for i in range(6) for l in "ae"}
    sa = [{"id": cid, "pass": False} for cid in cases]
    sb = [{"id": cid, "pass": cid.startswith(("c0", "c1", "c2", "c3", "c4", "c5"))} for cid in cases]
    r = REPORT.group_sign_test(sa, sb, cases)
    assert (r["groups"], r["candidate_better"], r["candidate_worse"], r["ties"]) == (6, 6, 0, 0)
    assert r["p_value_two_sided"] == round(2 / 64, 4)
    same = REPORT.group_sign_test(sa, sa, cases)
    assert same["p_value_two_sided"] is None and same["ties"] == 6


def test_provider_error_is_a_failure_in_the_runner_scorer():
    case = b.load_split("dev")[0]
    rec = {"id": case["id"], "config": "baseline", "reply": "", "model": "k", "error": "HTTP 502", "latency_s": 1.0}
    assert not RUN.score_record(case, rec)["pass"]
