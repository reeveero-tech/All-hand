#!/usr/bin/env python3
"""NetworkClientAgent0524 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single template body in
_templates/network_client_template.py; this historical module now just re-exports the
generated class and the create() function so any existing importer keeps working.
"""

from agent_civilization.agents.specialists.network_client import NetworkClientAgent0524 as NetworkClientAgent0524  # noqa: F401

__all__ = ["NetworkClientAgent0524", "create"]


def create(hub, aid: int = 524):
    """The historical create() — kept with the same default."""
    return NetworkClientAgent0524(name="network_client_0524", hub=hub, agent_id=aid)
