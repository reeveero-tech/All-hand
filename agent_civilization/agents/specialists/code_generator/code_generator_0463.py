#!/usr/bin/env python3
"""CodeGeneratorAgent0463 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single template body in
_templates/code_generator_template.py; this historical module now just re-exports the
generated class and the create() function so any existing importer keeps working.
"""

from agent_civilization.agents.specialists.code_generator import CodeGeneratorAgent0463 as CodeGeneratorAgent0463  # noqa: F401

__all__ = ["CodeGeneratorAgent0463", "create"]


def create(hub, aid: int = 463):
    """The historical create() — kept with the same default."""
    return CodeGeneratorAgent0463(name="code_generator_0463", hub=hub, agent_id=aid)
