"""R-177 (D-141): `--all` مع ملمح ثقيل يجب أن يمرر تجاوز Q-023 المزدوج إلى كل خدمة.

العيب (اكتُشف في تشغيل Codespaces المخفض بموافقة المالك): `run_all_baselines` و`_run_each`
لا يمرران `allow_dedicated_env` إلى `run_service`، فيرفض الحرس كل خدمة حتى مع
`--allow-dedicated-env` و`AGOS_ALLOW_BIG_LOAD=1` معًا. الرفض بلا تجاوز يبقى الافتراض.
"""
import importlib.util
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _mod():
    spec = importlib.util.spec_from_file_location("r177_ps_override", ROOT / "scripts" / "r177_per_service.py")
    m = importlib.util.module_from_spec(spec)
    sys.modules["r177_ps_override"] = m
    spec.loader.exec_module(m)
    return m


def _fake_run_service(m, seen):
    real_params = m.profile_params

    def fake(name, sut_url=None, api_key=None, out_dir=None, extra_thresholds=None,
             allow_dedicated_env=False, profile="baseline", tenants=None, inject=None,
             monitor=True, storage=None):
        data = m.load_services()
        params = real_params(data["services"][name], data, profile, allow_dedicated_env)
        seen.append((name, allow_dedicated_env, params["duration_s"]))
        return {"service": name, "classification": "measured", "ok": True}
    return fake


def test_all_heavy_with_double_override_reaches_every_service(monkeypatch, tmp_path):
    m = _mod()
    seen = []
    monkeypatch.setattr(m, "run_service", _fake_run_service(m, seen))
    monkeypatch.setenv("AGOS_ALLOW_BIG_LOAD", "1")
    s = m.run_all_baselines(sut_url="http://127.0.0.1:9", out_dir=tmp_path, profile="stress",
                            services=["api-health", "api-auth"], allow_dedicated_env=True)
    assert [x[0] for x in seen] == ["api-health", "api-auth"]
    assert all(x[1] is True for x in seen)
    assert all(x[2] == 300 for x in seen)  # stress duration unchanged (no reduction)
    assert s["counts"]["measured"] == 2


def test_all_heavy_without_flag_still_refused(monkeypatch, tmp_path):
    m = _mod()
    seen = []
    monkeypatch.setattr(m, "run_service", _fake_run_service(m, seen))
    monkeypatch.setenv("AGOS_ALLOW_BIG_LOAD", "1")
    with pytest.raises(SystemExit):
        m.run_all_baselines(sut_url="http://127.0.0.1:9", out_dir=tmp_path, profile="stress",
                            services=["api-health"])
    assert seen == []


def test_all_heavy_flag_without_env_still_refused(monkeypatch, tmp_path):
    m = _mod()
    seen = []
    monkeypatch.setattr(m, "run_service", _fake_run_service(m, seen))
    monkeypatch.delenv("AGOS_ALLOW_BIG_LOAD", raising=False)
    with pytest.raises(SystemExit):
        m.run_all_baselines(sut_url="http://127.0.0.1:9", out_dir=tmp_path, profile="spike",
                            services=["api-health"], allow_dedicated_env=True)
    assert seen == []
