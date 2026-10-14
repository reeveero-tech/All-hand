#!/usr/bin/env python3
"""ApiIntegratorAgent0941 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single base class in
_templates/api_integrator_base.py; this historical module re-exports the generated
class (a type() subclass with the historical name and agent_id default) and
the create() function.
"""

from agent_civilization.agents.specialists.api_integrator import ApiIntegratorAgent0941 as ApiIntegratorAgent0941  # noqa: F401
from agent_civilization.agents.specialists._templates._factory import generate_create

__all__ = ["ApiIntegratorAgent0941", "create"]

create = generate_create("api_integrator", ApiIntegratorAgent0941, 941)
