#!/usr/bin/env python3
"""ReviewerAgent0249 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single base class in
_templates/reviewer_base.py; this historical module re-exports the generated
class (a type() subclass with the historical name and agent_id default) and
the create() function.
"""

from agent_civilization.agents.specialists.reviewer import ReviewerAgent0249 as ReviewerAgent0249  # noqa: F401
from agent_civilization.agents.specialists._templates._factory import generate_create

__all__ = ["ReviewerAgent0249", "create"]

create = generate_create("reviewer", ReviewerAgent0249, 249)
