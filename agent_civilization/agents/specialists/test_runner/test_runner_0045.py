#!/usr/bin/env python3
"""TestRunnerAgent0045 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single template body in
_templates/test_runner_template.py; this historical module now just re-exports the
generated class and the create() function so any existing importer keeps working.
"""

from agent_civilization.agents.specialists.test_runner import TestRunnerAgent0045 as TestRunnerAgent0045  # noqa: F401

__all__ = ["TestRunnerAgent0045", "create"]


def create(hub, aid: int = 45):
    """The historical create() — kept with the same default."""
    return TestRunnerAgent0045(name="test_runner_0045", hub=hub, agent_id=aid)
