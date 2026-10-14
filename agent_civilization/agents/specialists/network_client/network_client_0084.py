#!/usr/bin/env python3
"""NetworkClientAgent0084 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single template body in
_templates/network_client_template.py; this historical module now just re-exports the
generated class and the create() function so any existing importer keeps working.
"""

from agent_civilization.agents.specialists.network_client import NetworkClientAgent0084 as NetworkClientAgent0084  # noqa: F401

__all__ = ["NetworkClientAgent0084", "create"]


def create(hub, aid: int = 84):
    """The historical create() — kept with the same default."""
    return NetworkClientAgent0084(name="network_client_0084", hub=hub, agent_id=aid)
