#!/usr/bin/env python3
"""BuilderAgent0946 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single template body in
_templates/builder_template.py; this historical module now just re-exports the
generated class and the create() function so any existing importer keeps working.
"""

from agent_civilization.agents.specialists.builder import BuilderAgent0946 as BuilderAgent0946  # noqa: F401

__all__ = ["BuilderAgent0946", "create"]


def create(hub, aid: int = 946):
    """The historical create() — kept with the same default."""
    return BuilderAgent0946(name="builder_0946", hub=hub, agent_id=aid)
