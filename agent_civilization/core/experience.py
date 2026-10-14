#!/usr/bin/env python3
"""
Experience layer — memory and learning on the live execution path (R-103, D-046).

Before R-103, ``agos_kernel.agent_production`` (MemorySystem, LearningRuntime)
was reachable only from tests: no task run through the API was remembered and
nothing was learned from it.  Now every real task — /api/tasks, /api/chat,
/civilization/*, pipelines, both agent implementations — goes through
``run_with_experience``:

before the task
  * ``memory.recalled``: earlier tasks of the same specialization whose words
    overlap this one (Jaccard over word sets, >= MIN_RECALL_SCORE), newest
    memories first among equal scores — prompt, answer, ok, age, score;
  * ``learning.prior``: what LearningRuntime measured so far for this
    specialization and input kind (samples, success rate, mean duration).
after the task
  * the outcome is stored as an episodic memory and recorded in LearningRuntime.
behavioural effect (the one place memory changes a result)
  * ``researcher`` depends on a live web API.  When the live call fails and
    memory holds a *successful* researcher result for a near-identical query
    (score >= FALLBACK_MIN_SCORE), that result is returned — marked
    ``degraded: true`` / ``source: "memory"`` with its age and score, and the
    live error kept in ``live_error``.  Nothing else is ever substituted.

What it does NOT do (stated in D-046): handlers are deterministic and are not
changed by learning; learning measures real outcomes and reports them.

Scope: one memory per server process, shared by all clients — the same
visibility as GET /api/tasks (single-tenant deployment).  Limits:
AGOS_MEMORY_MAX entries (default 2000, least important/oldest pruned), text
fields truncated, no tool_output kept except researcher's (<= 4 KB).
AGOS_MEMORY=0 disables the layer (outcomes then carry no ``experience``).
AGOS_MEMORY_FILE persists memory as JSON (atomic write every
PERSIST_EVERY stores and at shutdown; learning is rebuilt from it at load).
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from datetime import datetime
from typing import Any, Awaitable, Callable, Dict, List, Optional

from agos_kernel.agent_production.learning_runtime import LearningRuntime
from agos_kernel.agent_production.memory_system import MemorySystem, MemoryType

logger = logging.getLogger("agos.experience")

MIN_RECALL_SCORE = 0.25
FALLBACK_MIN_SCORE = 0.6
RECALL_LIMIT = 3
PERSIST_EVERY = 25
_TEXT_LIMIT = 500
_TOOL_OUTPUT_LIMIT = 4096
_WORD = re.compile(r"[\w\u0600-\u06FF]{2,}")

_memory: Optional[MemorySystem] = None
_learning: Optional[LearningRuntime] = None
_stores_since_persist = 0
_loaded = False


def enabled() -> bool:
    return os.getenv("AGOS_MEMORY", "1").strip() != "0"


def _max_entries() -> int:
    try:
        return max(10, int(os.getenv("AGOS_MEMORY_MAX", "2000")))
    except ValueError:
        return 2000


def memory() -> MemorySystem:
    global _memory
    if _memory is None:
        _memory = MemorySystem(max_entries=_max_entries(),
                               persistence_file=os.getenv("AGOS_MEMORY_FILE") or None)
    return _memory


def learning() -> LearningRuntime:
    global _learning
    if _learning is None:
        _learning = LearningRuntime()
    return _learning


def reset() -> None:
    """Drop all memory and learning (tests, DELETE /api/memory)."""
    global _memory, _learning, _stores_since_persist, _loaded
    _memory, _learning, _stores_since_persist, _loaded = None, None, 0, False


# ---------------------------------------------------------------- helpers

def _work_text(work: Dict[str, Any]) -> str:
    parts = [str(work.get(k) or "") for k in ("prompt", "text", "query", "goal", "message")]
    data = work.get("data")
    if data is not None:
        parts.append(data if isinstance(data, str) else json.dumps(data, ensure_ascii=False, default=str))
    return " ".join(p for p in parts if p)[:4000]


def words(text: str) -> set:
    return {w.lower() for w in _WORD.findall(text or "")}


def jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def input_kind(work: Dict[str, Any]) -> str:
    """Coarse shape of the input — the 'approach' dimension LearningRuntime tracks."""
    data = work.get("data")
    # R-114: classify the stage's own input, not the previous-stages summary the
    # orchestrator appends (R-113 analysis: almost every stage was classified as
    # "numbers" because earlier stages' statistics were in that summary).
    text = str(work.get("prompt") or "").split("\n\n=== Previous stages summary ===", 1)[0]
    if isinstance(data, list):
        return "numbers" if data and all(isinstance(x, (int, float)) for x in data) else "list"
    if isinstance(data, dict):
        return "object"
    blob = (data if isinstance(data, str) else "") + "\n" + text
    if re.search(r"https?://", blob):
        return "url"
    if re.search(r"^\s*(def |class |import |from \w+ import)", blob, re.M) or "```" in blob:
        return "code"
    if len(re.findall(r"-?\d+(?:\.\d+)?", blob)) >= 2:
        return "numbers"
    return "text" if blob.strip() else "empty"


def _answer_of(result: Dict[str, Any]) -> str:
    v = result.get("answer") if result.get("ok") else result.get("error")
    return str(v or "")[:_TEXT_LIMIT]


async def _ensure_loaded() -> None:
    """Load persisted memory once and replay it into LearningRuntime."""
    global _loaded
    if _loaded:
        return
    _loaded = True
    mem = memory()
    if not mem._persistence_file:
        return
    await mem.load()
    for entry in sorted(mem._memories.values(), key=lambda e: e.created_at):
        md = entry.metadata
        if md.get("layer") == "experience":
            learning().record_task(agent_id=md.get("agent", "?"), task_type=md["specialization"],
                                   approach=md.get("input_kind", "text"), success=bool(md.get("ok")),
                                   duration_ms=float(md.get("elapsed_ms", 0.0)))


async def persist() -> None:
    if _memory is not None and _memory._persistence_file:
        await _memory.persist()


# ---------------------------------------------------------------- recall / learning

async def recall(specialization: str, work: Dict[str, Any], limit: int = RECALL_LIMIT) -> List[Dict[str, Any]]:
    mem = memory()
    query_words = words(_work_text(work))
    ids = mem._index_by_tag.get(f"spec:{specialization}", set())
    now = datetime.utcnow()
    scored = []
    for mid in ids:
        e = mem._memories.get(mid)
        if e is None:
            continue
        score = jaccard(query_words, set(e.metadata.get("words") or []))
        if score >= MIN_RECALL_SCORE:
            scored.append((score, e.created_at, e))
    scored.sort(key=lambda t: (t[0], t[1]), reverse=True)
    out = []
    for score, _, e in scored[:limit]:
        e.touch()
        md = e.metadata
        out.append({"memory_id": e.memory_id, "score": round(score, 3), "ok": md.get("ok"),
                    "prompt": md.get("prompt", ""), "answer": md.get("answer", ""),
                    "agent": md.get("agent"), "age_s": round((now - e.created_at).total_seconds(), 1)})
    return out


def prior_learning(specialization: str, kind: str) -> Dict[str, Any]:
    key = f"{specialization}:{kind}"
    pattern = learning()._global_patterns.get(key)
    if pattern is None:
        return {"specialization": specialization, "input_kind": kind, "samples": 0}
    return {"specialization": specialization, "input_kind": kind, "samples": pattern.sample_count,
            "success_rate": round(pattern.success_rate, 3),
            "avg_duration_ms": round(pattern.avg_duration_ms, 1)}


async def _researcher_fallback(work: Dict[str, Any], result: Dict[str, Any],
                               recalled: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if result.get("ok") or not str(result.get("error", "")).startswith("search:"):
        return None  # only a failed live call, never an empty query
    for r in recalled:
        if r["ok"] and r["score"] >= FALLBACK_MIN_SCORE:
            entry = memory()._memories.get(r["memory_id"])
            if entry is None or "tool_output" not in entry.metadata:
                continue
            return {"ok": True, "degraded": True, "source": "memory",
                    "answer": (f"live search failed ({result['error'][:120]}); remembered result "
                               f"from {r['age_s']}s ago (similarity {r['score']}) for "
                               f"'{r['prompt'][:80]}': {r['answer'][:300]}"),
                    "tool_output": entry.metadata["tool_output"],
                    "live_error": result["error"], "memory_id": r["memory_id"]}
    return None


async def _store(specialization: str, agent: str, work: Dict[str, Any], result: Dict[str, Any],
                 kind: str, elapsed_ms: float) -> str:
    global _stores_since_persist
    ok = bool(result.get("ok"))
    text = _work_text(work)
    prompt = str(work.get("prompt") or text)[:_TEXT_LIMIT]
    answer = _answer_of(result)
    metadata: Dict[str, Any] = {
        "layer": "experience", "specialization": specialization, "agent": agent,
        "prompt": prompt, "answer": answer, "ok": ok, "input_kind": kind,
        "elapsed_ms": round(elapsed_ms, 1), "words": sorted(words(text))[:200],
    }
    if specialization == "researcher" and ok and not result.get("degraded"):
        blob = json.dumps(result.get("tool_output") or {}, ensure_ascii=False, default=str)
        if len(blob) <= _TOOL_OUTPUT_LIMIT:
            metadata["tool_output"] = result.get("tool_output") or {}
    mid = await memory().store(
        content=f"{specialization}: {prompt[:200]} -> {'ok' if ok else 'failed'}: {answer[:200]}",
        memory_type=MemoryType.EPISODIC,
        tags={f"spec:{specialization}", "success" if ok else "failure", f"kind:{kind}"},
        metadata=metadata, importance=0.7 if ok else 0.9)
    _stores_since_persist += 1
    if _stores_since_persist >= PERSIST_EVERY:
        _stores_since_persist = 0
        await persist()
    return mid


# ---------------------------------------------------------------- entry point

async def run_with_experience(specialization: str, agent: str, work: Dict[str, Any],
                              call: Callable[[], Awaitable[Dict[str, Any]]]):
    """Run ``call`` (the real handler) with memory and learning around it.

    Returns ``(result, experience)``; ``experience`` is None when disabled or if
    the layer itself failed (the task result is never lost because of it)."""
    if not enabled():
        return await call(), None
    try:
        await _ensure_loaded()
        kind = input_kind(work)
        recalled = await recall(specialization, work)
        prior = prior_learning(specialization, kind)
    except Exception as exc:  # noqa: BLE001 — memory must never break a task
        logger.warning("experience recall failed: %s", exc)
        return await call(), None
    started = time.perf_counter()
    result = await call()
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    try:
        fallback = None
        if specialization == "researcher" and isinstance(result, dict):
            fallback = await _researcher_fallback(work, result, recalled)
        live_result = result
        if fallback is not None:
            result = fallback
        mid = await _store(specialization, agent, work, live_result if isinstance(live_result, dict) else {},
                           kind, elapsed_ms)
        learning().record_task(agent_id=agent, task_type=specialization, approach=kind,
                               success=bool(isinstance(live_result, dict) and live_result.get("ok")),
                               duration_ms=elapsed_ms)
        experience = {"memory": {"recalled": recalled, "stored_id": mid,
                                 "fallback_used": fallback["memory_id"] if fallback else None},
                      "learning": {"prior": prior,
                                   "after": prior_learning(specialization, kind)}}
        return result, experience
    except Exception as exc:  # noqa: BLE001
        logger.warning("experience store failed: %s", exc)
        return result, None


def stats() -> Dict[str, Any]:
    mem = memory()
    by_spec: Dict[str, Dict[str, Any]] = {}
    for p in learning()._global_patterns.values():
        s = by_spec.setdefault(p.task_type, {"samples": 0, "successes": 0.0, "by_input_kind": {}})
        s["samples"] += p.sample_count
        s["successes"] += p.success_rate * p.sample_count
        s["by_input_kind"][p.approach] = {"samples": p.sample_count, "success_rate": round(p.success_rate, 3),
                                          "avg_duration_ms": round(p.avg_duration_ms, 1)}
    for s in by_spec.values():
        s["success_rate"] = round(s.pop("successes") / s["samples"], 3) if s["samples"] else 0.0
    return {"enabled": enabled(), "memories": len(mem._memories), "max_entries": mem._max_entries,
            "persistence_file": bool(mem._persistence_file),
            "learning": {"tasks": sum(e.total_tasks for e in learning()._experience.values()),
                         "by_specialization": by_spec}}


async def search(q: str = "", specialization: Optional[str] = None, limit: int = 20) -> List[Dict[str, Any]]:
    mem = memory()
    qw = words(q)
    out = []
    for e in mem._memories.values():
        md = e.metadata
        if md.get("layer") != "experience":
            continue
        if specialization and md.get("specialization") != specialization:
            continue
        score = jaccard(qw, set(md.get("words") or [])) if qw else None
        if qw and not score:
            continue
        out.append((score or 0.0, e.created_at, {
            "memory_id": e.memory_id, "specialization": md.get("specialization"), "agent": md.get("agent"),
            "prompt": md.get("prompt"), "answer": md.get("answer"), "ok": md.get("ok"),
            "input_kind": md.get("input_kind"), "created_at": e.created_at.isoformat() + "Z",
            **({"score": round(score, 3)} if qw else {})}))
    out.sort(key=lambda t: (t[0], t[1]), reverse=True)
    return [o for _, _, o in out[:max(1, min(limit, 100))]]
