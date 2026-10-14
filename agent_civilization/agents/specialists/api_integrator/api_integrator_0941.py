#!/usr/bin/env python3
"""ApiIntegratorAgent0941 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single template body in
_templates/api_integrator_template.py; this historical module now just re-exports the
generated class and the create() function so any existing importer keeps working.
"""

from agent_civilization.agents.specialists.api_integrator import ApiIntegratorAgent0941 as ApiIntegratorAgent0941  # noqa: F401

__all__ = ["ApiIntegratorAgent0941", "create"]


def create(hub, aid: int = 941):
    """The historical create() — kept with the same default."""
    return ApiIntegratorAgent0941(name="api_integrator_0941", hub=hub, agent_id=aid)
