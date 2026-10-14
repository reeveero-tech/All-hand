#!/usr/bin/env python3
"""ApiIntegratorAgent0561 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single template body in
_templates/api_integrator_template.py; this historical module now just re-exports the
generated class and the create() function so any existing importer keeps working.
"""

from agent_civilization.agents.specialists.api_integrator import ApiIntegratorAgent0561 as ApiIntegratorAgent0561  # noqa: F401

__all__ = ["ApiIntegratorAgent0561", "create"]


def create(hub, aid: int = 561):
    """The historical create() — kept with the same default."""
    return ApiIntegratorAgent0561(name="api_integrator_0561", hub=hub, agent_id=aid)
