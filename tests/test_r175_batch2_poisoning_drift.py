"""R-175 batch 2: catalog poisoning and schema drift — fail-closed proofs.

The 10K_TOOLS_SPEC_AR.md adversarial list requires testing «tool poisoning،
schema drift». These are characterization tests of the store's integrity
rules (they pass on main — the batch's red is the throughput module's
ImportError); they pin the fail-closed behavior so a later change cannot
silently weaken it:

* a tampered row (edited body, digest kept) is REFUSED on read — integrity
  error, never served;
* a poisoned row with a digest consistent with its poison is REFUSED by
  record validation (bogus lifecycle_state is not a state);
* a poisoned registry stops ``certify_batch`` safely — reported, no crash;
* seeding never overwrites the stored record (drift reported, stored wins)
  and never resurrects a removed version (tombstone).
"""
from __future__ import annotations

import json
import sqlite3

import pytest

from agent_civilization.core.tool_catalog_bench import (
    make_record, make_records, to_record)
from agent_civilization.core.tool_certification import certify_batch
from agent_civilization.core.tool_registry import RegistryError
from agent_civilization.core.tool_registry_store import (
    PersistentToolRegistry, RegistryStoreError)

_EVIDENCE = "smoke pass; contract v1.0 verified; security: allowlist+timeout"


@pytest.fixture()
def reg_path(tmp_path):
    return str(tmp_path / "poison.sqlite3")


def _open(path):
    return PersistentToolRegistry(path)


# -- poisoning: the tampered row is refused on read --------------------------------

def test_tampered_row_is_refused_on_read(reg_path):
    reg = _open(reg_path)
    reg.seed(make_records(3), actor="test:r175b2")
    reg.close()
    # a hostile/buggy writer edits the body but keeps the stored digest:
    conn = sqlite3.connect(reg_path)
    row = conn.execute("SELECT record_json FROM tool_records WHERE tool_id=?",
                       ("bench.00000",)).fetchone()
    body = json.loads(row[0])
    body["description"] = "poisoned: misleading description"
    conn.execute("UPDATE tool_records SET record_json=? WHERE tool_id=?",
                 (json.dumps(body), "bench.00000"))
    conn.commit()
    conn.close()
    # the refusal fires at CONSTRUCTION (eager sync): the poisoned store is
    # never even opened, let alone served:
    with pytest.raises(RegistryStoreError) as ei:
        _open(reg_path)
    assert "integrity" in str(ei.value)


def test_digest_consistent_poison_is_refused_by_validation(reg_path):
    reg = _open(reg_path)
    reg.seed(make_records(1), actor="test:r175b2")
    reg.close()
    # poison that recomputes its digest still fails: 'zombie' is not a state:
    conn = sqlite3.connect(reg_path)
    row = conn.execute("SELECT record_json FROM tool_records WHERE tool_id=?",
                       ("bench.00000",)).fetchone()
    body = json.loads(row[0])
    body["lifecycle_state"] = "zombie"
    import hashlib
    digest = hashlib.sha256(json.dumps(
        body, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode()).hexdigest()
    conn.execute("UPDATE tool_records SET record_json=?, digest=? WHERE tool_id=?",
                 (json.dumps(body), digest, "bench.00000"))
    conn.commit()
    conn.close()
    with pytest.raises((RegistryStoreError, ValueError)):
        _open(reg_path)


def test_poisoned_registry_stops_the_batch_safely(reg_path):
    reg = _open(reg_path)
    reg.seed(make_records(2), actor="test:r175b2")
    # poison the shared file BEHIND the open registry (WAL) and bump the
    # revision so the next operation re-reads the poisoned rows:
    conn = sqlite3.connect(reg_path)
    conn.execute("UPDATE tool_records SET record_json='{bad json' WHERE tool_id=?",
                 ("bench.00000",))
    conn.execute("UPDATE tool_meta SET v = v + 1 WHERE k='revision'")
    conn.commit()
    conn.close()
    items = [{"tool_id": f"bench.{i:05d}", "version": "1.0.0",
              "evidence": _EVIDENCE} for i in range(2)]
    # no exception escapes; the safe stop reports the store failure:
    report = certify_batch(reg, items, actor="test:r175b2", phase=1)
    assert report["error_type"] == "RegistryStoreError"
    assert report["certified"] == []
    reg.close()


# -- schema drift: stored wins, never silently rewritten ----------------------------

def test_seed_reports_drift_and_the_stored_record_wins(reg_path):
    reg = _open(reg_path)
    reg.register(make_record("bench.keep", description="original description"),
                 actor="test:r175b2")
    # the code's seed changed (schema/description drift); the stored wins:
    drifted = to_record(make_record("bench.keep",
                                     description="drifted description"))
    drift = reg.seed([drifted], actor="test:r175b2")
    assert drift == ["bench.keep@1.0.0"]
    assert reg.get("bench.keep", "1.0.0").description == "original description"
    reg.close()


def test_seed_registers_the_missing_in_one_transaction(reg_path):
    reg = _open(reg_path)
    drift = reg.seed(make_records(10), actor="test:r175b2")
    assert drift == []
    assert len(reg) == 10
    assert reg.versions("bench.00007") == ["1.0.0"]
    reg.close()


def test_removed_version_is_not_resurrected_by_seeding(reg_path):
    reg = _open(reg_path)
    rec = make_record("bench.dead")
    reg.seed([to_record(rec)], actor="test:r175b2")
    reg.remove("bench.dead", "1.0.0", actor="test:r175b2")
    with pytest.raises(RegistryError):
        reg.get("bench.dead", "1.0.0")
    # the tombstone: the same key cannot come back, even with the same content:
    drift = reg.seed([to_record(rec)], actor="test:r175b2")
    assert drift == []
    with pytest.raises(RegistryError):
        reg.get("bench.dead", "1.0.0")
    reg.close()


def test_misleading_description_is_still_a_valid_record_not_a_certification(reg_path):
    """Poisoning of METADATA (a misleading description) is not a store error —
    it is a certification-refusal concern: the walk demands real evidence and
    the operator reviews descriptions. Pinned here so the boundary is explicit."""
    reg = _open(reg_path)
    reg.register(make_record("bench.liar", description="totally safe, trust me"),
                 actor="test:r175b2")
    report = certify_batch(reg, [{"tool_id": "bench.liar", "version": "1.0.0",
                                 "evidence": _EVIDENCE}], actor="test:r175b2")
    assert report["certified"] == ["bench.liar@1.0.0"]
    reg.close()
