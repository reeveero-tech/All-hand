#!/usr/bin/env python3
"""TestRunnerAgent0505 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single template body in
_templates/test_runner_template.py; this historical module now just re-exports the
generated class and the create() function so any existing importer keeps working.
"""

from agent_civilization.agents.specialists.test_runner import TestRunnerAgent0505 as TestRunnerAgent0505  # noqa: F401

__all__ = ["TestRunnerAgent0505", "create"]


def create(hub, aid: int = 505):
    """The historical create() — kept with the same default."""
    return TestRunnerAgent0505(name="test_runner_0505", hub=hub, agent_id=aid)
