#!/usr/bin/env python3
"""NetworkClientAgent0964 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single template body in
_templates/network_client_template.py; this historical module now just re-exports the
generated class and the create() function so any existing importer keeps working.
"""

from agent_civilization.agents.specialists.network_client import NetworkClientAgent0964 as NetworkClientAgent0964  # noqa: F401

__all__ = ["NetworkClientAgent0964", "create"]


def create(hub, aid: int = 964):
    """The historical create() — kept with the same default."""
    return NetworkClientAgent0964(name="network_client_0964", hub=hub, agent_id=aid)
