#!/usr/bin/env python3
"""ApiIntegratorAgent0901 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single template body in
_templates/api_integrator_template.py; this historical module now just re-exports the
generated class and the create() function so any existing importer keeps working.
"""

from agent_civilization.agents.specialists.api_integrator import ApiIntegratorAgent0901 as ApiIntegratorAgent0901  # noqa: F401

__all__ = ["ApiIntegratorAgent0901", "create"]


def create(hub, aid: int = 901):
    """The historical create() — kept with the same default."""
    return ApiIntegratorAgent0901(name="api_integrator_0901", hub=hub, agent_id=aid)
