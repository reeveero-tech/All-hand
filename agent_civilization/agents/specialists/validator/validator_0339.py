#!/usr/bin/env python3
"""ValidatorAgent0339 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single template body in
_templates/validator_template.py; this historical module now just re-exports the
generated class and the create() function so any existing importer keeps working.
"""

from agent_civilization.agents.specialists.validator import ValidatorAgent0339 as ValidatorAgent0339  # noqa: F401

__all__ = ["ValidatorAgent0339", "create"]


def create(hub, aid: int = 339):
    """The historical create() — kept with the same default."""
    return ValidatorAgent0339(name="validator_0339", hub=hub, agent_id=aid)
