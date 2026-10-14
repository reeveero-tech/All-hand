#!/usr/bin/env python3
"""TestRunnerAgent0485 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single template body in
_templates/test_runner_template.py; this historical module now just re-exports the
generated class and the create() function so any existing importer keeps working.
"""

from agent_civilization.agents.specialists.test_runner import TestRunnerAgent0485 as TestRunnerAgent0485  # noqa: F401

__all__ = ["TestRunnerAgent0485", "create"]


def create(hub, aid: int = 485):
    """The historical create() — kept with the same default."""
    return TestRunnerAgent0485(name="test_runner_0485", hub=hub, agent_id=aid)
