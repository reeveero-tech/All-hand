#!/usr/bin/env python3
"""
Real specialist runtime.

This module is the public entry-point for real (non-simulated) agent work.
It delegates every specialization to the exhaustive handler set in
`agent_civilization.core.agents.real_dispatcher`. Nothing in this file
fabricates data; each call performs a genuine parse, syscall, computation,
or HTTP request.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from agent_civilization.core.agents.real_dispatcher import (
    dispatch as _dispatch,
    coverage as _coverage,
)


async def run_real(specialization: str, prompt: str,
                   data: Optional[Any] = None) -> Dict[str, Any]:
    """Execute a real task for the given specialization."""
    result = await _dispatch(specialization, {"prompt": prompt, "data": data})
    if result is None:
        return {"ok": False, "specialization": specialization,
                "error": f"no real implementation for '{specialization}'"}
    return {"specialization": specialization, **result}


def real_coverage() -> Dict[str, Any]:
    """Report which specializations are backed by real handlers."""
    return _coverage()


__all__ = ["run_real", "real_coverage"]
