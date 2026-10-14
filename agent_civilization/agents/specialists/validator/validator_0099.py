#!/usr/bin/env python3
"""ValidatorAgent0099 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single base class in
_templates/validator_base.py; this historical module re-exports the generated
class (a type() subclass with the historical name and agent_id default) and
the create() function.
"""

from agent_civilization.agents.specialists.validator import ValidatorAgent0099 as ValidatorAgent0099  # noqa: F401
from agent_civilization.agents.specialists._templates._factory import generate_create

__all__ = ["ValidatorAgent0099", "create"]

create = generate_create("validator", ValidatorAgent0099, 99)
