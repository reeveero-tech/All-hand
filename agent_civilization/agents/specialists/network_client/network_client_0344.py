#!/usr/bin/env python3
"""NetworkClientAgent0344 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single template body in
_templates/network_client_template.py; this historical module now just re-exports the
generated class and the create() function so any existing importer keeps working.
"""

from agent_civilization.agents.specialists.network_client import NetworkClientAgent0344 as NetworkClientAgent0344  # noqa: F401

__all__ = ["NetworkClientAgent0344", "create"]


def create(hub, aid: int = 344):
    """The historical create() — kept with the same default."""
    return NetworkClientAgent0344(name="network_client_0344", hub=hub, agent_id=aid)
