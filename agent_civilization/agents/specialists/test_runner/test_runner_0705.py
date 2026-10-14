#!/usr/bin/env python3
"""TestRunnerAgent0705 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single template body in
_templates/test_runner_template.py; this historical module now just re-exports the
generated class and the create() function so any existing importer keeps working.
"""

from agent_civilization.agents.specialists.test_runner import TestRunnerAgent0705 as TestRunnerAgent0705  # noqa: F401

__all__ = ["TestRunnerAgent0705", "create"]


def create(hub, aid: int = 705):
    """The historical create() — kept with the same default."""
    return TestRunnerAgent0705(name="test_runner_0705", hub=hub, agent_id=aid)
