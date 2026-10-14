#!/usr/bin/env python3
"""CodeGeneratorAgent0983 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single base class in
_templates/code_generator_base.py; this historical module re-exports the generated
class (a type() subclass with the historical name and agent_id default) and
the create() function.
"""

from agent_civilization.agents.specialists.code_generator import CodeGeneratorAgent0983 as CodeGeneratorAgent0983  # noqa: F401
from agent_civilization.agents.specialists._templates._factory import generate_create

__all__ = ["CodeGeneratorAgent0983", "create"]

create = generate_create("code_generator", CodeGeneratorAgent0983, 983)
