#!/usr/bin/env python3
"""CodeGeneratorAgent0183 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single template body in
_templates/code_generator_template.py; this historical module now just re-exports the
generated class and the create() function so any existing importer keeps working.
"""

from agent_civilization.agents.specialists.code_generator import CodeGeneratorAgent0183 as CodeGeneratorAgent0183  # noqa: F401

__all__ = ["CodeGeneratorAgent0183", "create"]


def create(hub, aid: int = 183):
    """The historical create() — kept with the same default."""
    return CodeGeneratorAgent0183(name="code_generator_0183", hub=hub, agent_id=aid)
