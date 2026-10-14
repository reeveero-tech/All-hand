#!/usr/bin/env python3
"""CodeGeneratorAgent0123 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single template body in
_templates/code_generator_template.py; this historical module now just re-exports the
generated class and the create() function so any existing importer keeps working.
"""

from agent_civilization.agents.specialists.code_generator import CodeGeneratorAgent0123 as CodeGeneratorAgent0123  # noqa: F401

__all__ = ["CodeGeneratorAgent0123", "create"]


def create(hub, aid: int = 123):
    """The historical create() — kept with the same default."""
    return CodeGeneratorAgent0123(name="code_generator_0123", hub=hub, agent_id=aid)
