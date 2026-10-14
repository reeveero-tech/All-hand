#!/usr/bin/env python3
"""NetworkClientAgent0224 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single template body in
_templates/network_client_template.py; this historical module now just re-exports the
generated class and the create() function so any existing importer keeps working.
"""

from agent_civilization.agents.specialists.network_client import NetworkClientAgent0224 as NetworkClientAgent0224  # noqa: F401

__all__ = ["NetworkClientAgent0224", "create"]


def create(hub, aid: int = 224):
    """The historical create() — kept with the same default."""
    return NetworkClientAgent0224(name="network_client_0224", hub=hub, agent_id=aid)
