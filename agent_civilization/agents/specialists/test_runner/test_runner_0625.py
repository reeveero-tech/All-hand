#!/usr/bin/env python3
"""TestRunnerAgent0625 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single base class in
_templates/test_runner_base.py; this historical module re-exports the generated
class (a type() subclass with the historical name and agent_id default) and
the create() function.
"""

from agent_civilization.agents.specialists.test_runner import TestRunnerAgent0625 as TestRunnerAgent0625  # noqa: F401
from agent_civilization.agents.specialists._templates._factory import generate_create

__all__ = ["TestRunnerAgent0625", "create"]

create = generate_create("test_runner", TestRunnerAgent0625, 625)
