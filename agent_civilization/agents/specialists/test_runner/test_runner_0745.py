#!/usr/bin/env python3
"""TestRunnerAgent0745 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single template body in
_templates/test_runner_template.py; this historical module now just re-exports the
generated class and the create() function so any existing importer keeps working.
"""

from agent_civilization.agents.specialists.test_runner import TestRunnerAgent0745 as TestRunnerAgent0745  # noqa: F401

__all__ = ["TestRunnerAgent0745", "create"]


def create(hub, aid: int = 745):
    """The historical create() — kept with the same default."""
    return TestRunnerAgent0745(name="test_runner_0745", hub=hub, agent_id=aid)
