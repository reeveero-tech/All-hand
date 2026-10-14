#!/usr/bin/env python3
"""ApiIntegratorAgent0501 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single template body in
_templates/api_integrator_template.py; this historical module now just re-exports the
generated class and the create() function so any existing importer keeps working.
"""

from agent_civilization.agents.specialists.api_integrator import ApiIntegratorAgent0501 as ApiIntegratorAgent0501  # noqa: F401

__all__ = ["ApiIntegratorAgent0501", "create"]


def create(hub, aid: int = 501):
    """The historical create() — kept with the same default."""
    return ApiIntegratorAgent0501(name="api_integrator_0501", hub=hub, agent_id=aid)
