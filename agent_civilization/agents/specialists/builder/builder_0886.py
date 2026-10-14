#!/usr/bin/env python3
"""BuilderAgent0886 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single template body in
_templates/builder_template.py; this historical module now just re-exports the
generated class and the create() function so any existing importer keeps working.
"""

from agent_civilization.agents.specialists.builder import BuilderAgent0886 as BuilderAgent0886  # noqa: F401

__all__ = ["BuilderAgent0886", "create"]


def create(hub, aid: int = 886):
    """The historical create() — kept with the same default."""
    return BuilderAgent0886(name="builder_0886", hub=hub, agent_id=aid)
