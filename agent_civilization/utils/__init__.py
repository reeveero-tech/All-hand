"""Payload assembly for the live data-processor handler (R-104c2, D-047 amendment).

Production consumer: ``real_dispatcher._data_processor`` imports
``build_llm_payload`` from here to assemble the prompt+data payload it sends to
the LLM-backed path (R-104c2 — data values were dropped before). The package's
role is live and tested — tests/test_r104_data_worker.py proves data-only
values reach the LLM through this assembly.
"""

import json
from typing import Any, Optional


def build_llm_payload(prompt: str, data: Optional[Any]) -> str:
    """The text payload for the LLM: the instruction plus the data, serialized.

    ``data`` may be a string (passed through) or any JSON-serializable value
    (dict/list) — rendered so the LLM sees both the instruction and the data.
    """
    if data is None:
        return prompt
    payload = prompt
    if not isinstance(data, str):
        data = json.dumps(data, ensure_ascii=False, default=str)
    return payload + "\n\n=== data ===\n" + data


__all__ = ["build_llm_payload"]
