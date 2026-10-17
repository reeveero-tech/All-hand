"""R-161b (G-161-5): the fail-closed staging gate of the manual live workflow.

(1) the gate's decision (scripts/r161_staging_gate.py) for every refusal
    reason, and that a refused gate makes no API call when ref/declaration
    are wrong and writes no output;
(2) the workflow structure (parsed YAML): the ``gate`` job has no environment
    and no secrets; the ``live`` job is the only one with an environment, its
    NAME comes only from the gate's output, it needs the gate and is
    conditioned on main + the gate's output; no job names r161-staging
    literally; only the runner step gets secrets.
Evidence class: static/unit. GitHub's own evaluation is not executed here.
"""
import io
import json
import pathlib
import sys
import urllib.error

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import r161_staging_gate as G  # noqa: E402

WF = ROOT / ".github" / "workflows" / "live-integration.yml"
MAIN = "refs/heads/main"
GOOD_ENV = {"name": "r161-staging",
            "protection_rules": [{"type": "required_reviewers", "reviewers": [{"type": "User", "reviewer": {"login": "o"}}]},
                                 {"type": "branch_policy"}],
            "deployment_branch_policy": {"protected_branches": False, "custom_branch_policies": True}}
GOOD_POL = {"total_count": 1, "branch_policies": [{"name": "main", "type": "branch"}]}


def _env(**over):
    e = json.loads(json.dumps(GOOD_ENV))
    e.update(over)
    return e


# ------------------------------------------------------------- decision

def test_everything_in_place_opens_the_gate():
    assert G.evaluate(MAIN, "owner-verified", 200, GOOD_ENV, GOOD_POL) == (True, [])


@pytest.mark.parametrize("args,why", [
    (("refs/heads/feature", "owner-verified", 200, GOOD_ENV, GOOD_POL), "not on main"),
    (("refs/tags/v1", "owner-verified", 200, GOOD_ENV, GOOD_POL), "not on main"),
    ((MAIN, "", 200, GOOD_ENV, GOOD_POL), "R161_STAGING_READY"),
    ((MAIN, "true", 200, GOOD_ENV, GOOD_POL), "R161_STAGING_READY"),
    ((MAIN, "owner-verified", 404, None, None), "does not exist"),
    ((MAIN, "owner-verified", 403, None, None), "cannot be read"),
    ((MAIN, "owner-verified", 0, None, None), "HTTP 0"),
    ((MAIN, "owner-verified", 200, _env(protection_rules=[]), GOOD_POL), "no required reviewers"),
    ((MAIN, "owner-verified", 200, _env(protection_rules=[{"type": "required_reviewers", "reviewers": []}]), GOOD_POL),
     "no required reviewers"),
    ((MAIN, "owner-verified", 200, _env(deployment_branch_policy=None), GOOD_POL), "custom deployment-branch"),
    ((MAIN, "owner-verified", 200, _env(deployment_branch_policy={"protected_branches": True,
                                                                    "custom_branch_policies": False}), GOOD_POL),
     "custom deployment-branch"),
    ((MAIN, "owner-verified", 200, GOOD_ENV, {"branch_policies": [{"name": "main"}, {"name": "dev"}]}), "exactly the branch main"),
    ((MAIN, "owner-verified", 200, GOOD_ENV, {"branch_policies": [{"name": "*"}]}), "exactly the branch main"),
    ((MAIN, "owner-verified", 200, GOOD_ENV, {"branch_policies": [{"name": "main", "type": "tag"}]}), "exactly the branch main"),
    ((MAIN, "owner-verified", 200, GOOD_ENV, None), "exactly the branch main"),
])
def test_each_missing_condition_closes_the_gate(args, why):
    ok, reasons = G.evaluate(*args)
    assert ok is False and any(why in r for r in reasons), reasons


@pytest.mark.parametrize("ref,ready", [("refs/heads/dev", "owner-verified"), (MAIN, ""), (MAIN, "yes")])
def test_closed_gate_makes_no_api_call_and_writes_no_output(monkeypatch, tmp_path, capsys, ref, ready):
    calls = []
    monkeypatch.setattr(G, "_get", lambda *a: calls.append(a) or (200, GOOD_ENV))
    out = tmp_path / "gh_output"
    for k, v in {"GATE_REF": ref, "GATE_READY": ready, "GITHUB_REPOSITORY": "o/r",
                 "GH_TOKEN": "t", "GITHUB_OUTPUT": str(out)}.items():
        monkeypatch.setenv(k, v)
    assert G.main() == 1
    assert calls == [] and not out.exists()
    assert "CLOSED" in capsys.readouterr().out


def test_missing_environment_closes_the_gate_and_never_creates_it(monkeypatch, tmp_path):
    """Only GET requests are ever made (a GET cannot create an environment)."""
    seen = []

    def fake_urlopen(req, timeout=0):
        seen.append((req.get_method(), req.full_url))
        raise urllib.error.HTTPError(req.full_url, 404, "Not Found", {}, io.BytesIO(b"{}"))
    monkeypatch.setattr(G.urllib.request, "urlopen", fake_urlopen)
    out = tmp_path / "gh_output"
    for k, v in {"GATE_REF": MAIN, "GATE_READY": "owner-verified", "GITHUB_REPOSITORY": "o/r",
                 "GH_TOKEN": "t", "GITHUB_OUTPUT": str(out)}.items():
        monkeypatch.setenv(k, v)
    assert G.main() == 1 and not out.exists()
    assert seen == [("GET", "https://api.github.com/repos/o/r/environments/r161-staging")]


def test_open_gate_writes_the_environment_output(monkeypatch, tmp_path):
    answers = iter([(200, GOOD_ENV), (200, GOOD_POL)])
    monkeypatch.setattr(G, "_get", lambda *a: next(answers))
    out = tmp_path / "gh_output"
    for k, v in {"GATE_REF": MAIN, "GATE_READY": "owner-verified", "GITHUB_REPOSITORY": "o/r",
                 "GH_TOKEN": "t", "GITHUB_OUTPUT": str(out)}.items():
        monkeypatch.setenv(k, v)
    assert G.main() == 0
    assert out.read_text() == "environment=r161-staging\n"


# ------------------------------------------------------------- workflow

@pytest.fixture(scope="module")
def wf():
    text = WF.read_text()
    d = yaml.safe_load(text)
    d["_text"] = text
    return d


def _on(d):
    return d.get("on", d.get(True))          # PyYAML reads the key `on` as True


def test_manual_only_and_least_privilege(wf):
    assert set(_on(wf)) == {"workflow_dispatch"}
    assert wf["permissions"] == {"contents": "read"}
    assert set(wf["jobs"]) == {"gate", "live"}


def test_gate_job_has_no_environment_and_no_secrets(wf):
    gate = wf["jobs"]["gate"]
    assert "environment" not in gate
    assert gate["permissions"] == {"contents": "read", "actions": "read"}
    dumped = json.dumps(gate)
    assert "secrets." not in dumped and "R161_STORAGE_KEY" not in dumped and "API_KEY" not in dumped
    step = next(s for s in gate["steps"] if s.get("id") == "gate")
    assert step["env"] == {"GATE_REF": "${{ github.ref }}", "GATE_READY": "${{ vars.R161_STAGING_READY }}",
                           "GH_TOKEN": "${{ github.token }}"}
    assert step["run"].strip() == "python3 scripts/r161_staging_gate.py"
    assert gate["outputs"] == {"environment": "${{ steps.gate.outputs.environment }}"}


def test_live_job_reaches_staging_only_through_the_gate(wf):
    live = wf["jobs"]["live"]
    assert live["needs"] == "gate"
    assert live["if"] == "needs.gate.outputs.environment == 'r161-staging' && github.ref == 'refs/heads/main'"
    assert live["environment"] == {"name": "${{ needs.gate.outputs.environment }}"}
    assert "permissions" not in live                         # inherits contents: read
    # the literal name never appears as an environment of any job
    for name, job in wf["jobs"].items():
        env = job.get("environment")
        assert env is None or "r161-staging" not in json.dumps(env), name


def test_without_the_gate_output_the_live_job_cannot_name_the_environment(wf):
    """Model of GitHub's evaluation for the two refusal cases: gate failed
    (output unset -> '') or branch not main. The `if` is false and the
    environment name expression yields '' — never 'r161-staging'."""
    live = wf["jobs"]["live"]

    def evaluate_if(gate_output, ref):
        return gate_output == "r161-staging" and ref == "refs/heads/main"
    for gate_output, ref in [("", "refs/heads/main"), ("", "refs/heads/dev"), ("r161-staging", "refs/heads/dev")]:
        assert evaluate_if(gate_output, ref) is False
        name = live["environment"]["name"].replace("${{ needs.gate.outputs.environment }}", gate_output)
        assert gate_output != "r161-staging" or ref != "refs/heads/main"
        assert name in ("", "r161-staging") and (name != "r161-staging" or ref != "refs/heads/main")


def test_secrets_only_in_the_runner_step_after_the_gate(wf):
    live = wf["jobs"]["live"]
    with_secrets = [s.get("name") for s in live["steps"] if "secrets." in json.dumps(s)]
    assert with_secrets == ["Run the integration runner"]
    names = [s.get("name") for s in live["steps"]]
    assert names.index("Run the integration runner") < names.index("Upload the redacted report") \
        < names.index("Fail unless every requested target passed")
    run = next(s for s in live["steps"] if s.get("name") == "Run the integration runner")["run"]
    assert "Check the report for secret values" in run and "p.unlink()" in run
    assert "${{" not in run                                   # inputs only via env
