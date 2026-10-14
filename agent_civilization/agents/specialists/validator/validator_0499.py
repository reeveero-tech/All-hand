#!/usr/bin/env python3
"""ValidatorAgent0499 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single template body in
_templates/validator_template.py; this historical module now just re-exports the
generated class and the create() function so any existing importer keeps working.
"""

from agent_civilization.agents.specialists.validator import ValidatorAgent0499 as ValidatorAgent0499  # noqa: F401

__all__ = ["ValidatorAgent0499", "create"]


def create(hub, aid: int = 499):
    """The historical create() — kept with the same default."""
    return ValidatorAgent0499(name="validator_0499", hub=hub, agent_id=aid)
