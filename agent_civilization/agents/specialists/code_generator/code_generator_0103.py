#!/usr/bin/env python3
"""CodeGeneratorAgent0103 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single template body in
_templates/code_generator_template.py; this historical module now just re-exports the
generated class and the create() function so any existing importer keeps working.
"""

from agent_civilization.agents.specialists.code_generator import CodeGeneratorAgent0103 as CodeGeneratorAgent0103  # noqa: F401

__all__ = ["CodeGeneratorAgent0103", "create"]


def create(hub, aid: int = 103):
    """The historical create() — kept with the same default."""
    return CodeGeneratorAgent0103(name="code_generator_0103", hub=hub, agent_id=aid)
