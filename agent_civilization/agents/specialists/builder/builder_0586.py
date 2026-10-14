#!/usr/bin/env python3
"""BuilderAgent0586 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single template body in
_templates/builder_template.py; this historical module now just re-exports the
generated class and the create() function so any existing importer keeps working.
"""

from agent_civilization.agents.specialists.builder import BuilderAgent0586 as BuilderAgent0586  # noqa: F401

__all__ = ["BuilderAgent0586", "create"]


def create(hub, aid: int = 586):
    """The historical create() — kept with the same default."""
    return BuilderAgent0586(name="builder_0586", hub=hub, agent_id=aid)
