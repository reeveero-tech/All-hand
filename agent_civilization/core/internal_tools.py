"""R-171 batch 1 — registry entries for the 20 internal specialist handlers
(``agent_civilization.core.agents.real_dispatcher._HANDLERS``).

The metadata below is DECLARED by hand from reading the handlers; the tests
cross-check every declaration that can be checked statically (network,
model-provider calls, sandboxed code execution, the handler set itself)
against the AST of the frozen dispatcher, so a drift fails CI instead of
silently lying. The dispatcher is read, never modified (R-169 freeze).

Every seeded entry is ``lifecycle_state="registered"``, ``test_status=
"not_tested"``, ``health_status="unknown"``: registering a tool is not
evidence that it works. Certification belongs to R-175.
"""
from __future__ import annotations

from typing import Any, Dict, List

from agent_civilization.core.tool_registry import ToolRecord, ToolRegistry

DISPATCHER = "agent_civilization.core.agents.real_dispatcher"
SEED_VERSION = "1.0.0"

WORK_INPUT_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {"prompt": {"type": "string"}, "goal": {"type": "string"},
                   "data": {}, "verification": {"type": ["object", "null"]}},
    "additionalProperties": True,
}
RESULT_OUTPUT_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "required": ["ok"],
    "properties": {"ok": {"type": "boolean"}, "error": {"type": "string"},
                   "tool_output": {}},
    "additionalProperties": True,
}

PUBLIC_HTTP = "public-http (safety.check_url: private ranges refused)"
DDG = ["api.duckduckgo.com", "html.duckduckgo.com"]
MYMEMORY = ["api.mymemory.translated.net"]
MODEL = "model_provider_call"
NET = "external_http_read"
EXEC = "sandboxed_code_execution"

# spec -> (capabilities, risk, side_effects, network_allowlist, timeout_ms, description)
_TABLE: Dict[str, tuple] = {
    "code_generator": (["code.generate"], "medium", [MODEL], [], 60_000,
                       "Generates Python code from a goal (model provider, deterministic craft fallback)."),
    "surgeon": (["code.patch"], "low", [], [], 30_000,
                "Applies a targeted local code fix; no execution, no network."),
    "test_runner": (["code.test", "code.execute"], "high", [EXEC], [], 60_000,
                    "Runs given or generated tests in the sandboxed Python runner."),
    "validator": (["data.validate", "code.validate"], "low", [], [], 30_000,
                  "Validates data/code structure locally."),
    "analyst": (["analysis.numeric", "analysis.text"], "low", [], [], 30_000,
                "Deterministic numeric/text analysis of the given input."),
    "data_processor": (["data.transform"], "low", [], [], 30_000,
                       "Transforms/aggregates the given data locally."),
    "db_manager": (["db.schema_design", "db.migration_rehearsal"], "medium",
                   [MODEL, "in_memory_sqlite"], [], 60_000,
                   "Designs a schema (model provider or heuristic) and rehearses it on in-memory SQLite."),
    "network_client": (["net.probe"], "medium", [NET], [PUBLIC_HTTP], 30_000,
                       "DNS + HTTP HEAD/GET probe of a public URL or host."),
    "api_integrator": (["net.fetch_json"], "medium", [NET], [PUBLIC_HTTP], 30_000,
                       "HTTP GET of a public URL (bounded body) and JSON parse."),
    "researcher": (["research.web_search", "research.citations"], "medium", [NET], DDG,
                   60_000, "Web search with cited sources."),
    "architect": (["design.architecture"], "low", [], [], 30_000,
                  "Deterministic architecture outline."),
    "strategist": (["planning.strategy"], "low", [], [], 30_000,
                   "Deterministic strategy/plan outline."),
    "reviewer": (["code.review"], "low", [], [], 30_000,
                 "Static code review findings (AST), no execution."),
    "monitor": (["system.metrics"], "low", ["reads_local_process_metrics"], [], 30_000,
                "Reads local process/host metrics (/proc)."),
    "educator": (["education.explain"], "medium", [MODEL, NET], ["html.duckduckgo.com"],
                 60_000, "Explains a topic (model provider, web search)."),
    "communicator": (["text.translate", "text.compose"], "medium", [MODEL, NET], MYMEMORY,
                     60_000, "Translation/composition (model provider, MyMemory API)."),
    "designer": (["design.ui"], "low", [], [], 30_000, "Deterministic design artifact (craft)."),
    "builder": (["build.scaffold"], "low", [], [], 30_000, "Deterministic project scaffold (craft)."),
    "modifier": (["code.modify", "code.equivalence_check"], "high", [EXEC], [], 60_000,
                 "Modifies code and checks behavioral equivalence in the sandboxed runner."),
    "self_developer": (["meta.self_test"], "high", [MODEL, NET, EXEC], ["*all internal tools*"],
                       120_000, "Runs the other internal handlers as a self-test (reaches all of them)."),
}


def internal_tool_records() -> List[ToolRecord]:
    out = []
    for spec in sorted(_TABLE):
        caps, risk, effects, allow, timeout, desc = _TABLE[spec]
        out.append(ToolRecord.model_validate({
            "tool_id": f"agos.internal.{spec}",
            "name": spec,
            "version": SEED_VERSION,
            "source": {"kind": "internal", "uri": DISPATCHER},
            "description": desc,
            "capabilities": caps,
            "input_schema": WORK_INPUT_SCHEMA,
            "output_schema": RESULT_OUTPUT_SCHEMA,
            "auth_scope": [],
            "tenant_scope": "request_tenant",
            "risk_class": risk,
            "side_effects": effects,
            "read_only": True,       # no handler changes external state (HEAD/GET only)
            "idempotent": True,      # retry adds no external side effect (may add cost)
            "requires_approval": False,
            "timeout_ms": timeout,
            "network_allowlist": allow,
            "health_status": "unknown",
            "test_status": "not_tested",
            "implementation_ref": f"{DISPATCHER}:_{spec}",
            "lifecycle_state": "registered",
            "telemetry_name": f"tool.internal.{spec}",
        }))
    return out


def seeded_registry(actor: str = "seed:internal_tools") -> ToolRegistry:
    reg = ToolRegistry()
    for rec in internal_tool_records():
        reg.register(rec, actor=actor)
    return reg
