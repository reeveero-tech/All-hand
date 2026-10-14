#!/usr/bin/env python3
"""ValidatorAgent0939 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single template body in
_templates/validator_template.py; this historical module now just re-exports the
generated class and the create() function so any existing importer keeps working.
"""

from agent_civilization.agents.specialists.validator import ValidatorAgent0939 as ValidatorAgent0939  # noqa: F401

__all__ = ["ValidatorAgent0939", "create"]


def create(hub, aid: int = 939):
    """The historical create() — kept with the same default."""
    return ValidatorAgent0939(name="validator_0939", hub=hub, agent_id=aid)
