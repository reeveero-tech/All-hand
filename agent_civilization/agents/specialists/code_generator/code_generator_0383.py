#!/usr/bin/env python3
"""CodeGeneratorAgent0383 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single template body in
_templates/code_generator_template.py; this historical module now just re-exports the
generated class and the create() function so any existing importer keeps working.
"""

from agent_civilization.agents.specialists.code_generator import CodeGeneratorAgent0383 as CodeGeneratorAgent0383  # noqa: F401

__all__ = ["CodeGeneratorAgent0383", "create"]


def create(hub, aid: int = 383):
    """The historical create() — kept with the same default."""
    return CodeGeneratorAgent0383(name="code_generator_0383", hub=hub, agent_id=aid)
