#!/usr/bin/env python3
"""TestRunnerAgent0105 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single template body in
_templates/test_runner_template.py; this historical module now just re-exports the
generated class and the create() function so any existing importer keeps working.
"""

from agent_civilization.agents.specialists.test_runner import TestRunnerAgent0105 as TestRunnerAgent0105  # noqa: F401

__all__ = ["TestRunnerAgent0105", "create"]


def create(hub, aid: int = 105):
    """The historical create() — kept with the same default."""
    return TestRunnerAgent0105(name="test_runner_0105", hub=hub, agent_id=aid)
