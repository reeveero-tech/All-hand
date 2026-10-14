#!/usr/bin/env python3
"""NetworkClientAgent0804 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single template body in
_templates/network_client_template.py; this historical module now just re-exports the
generated class and the create() function so any existing importer keeps working.
"""

from agent_civilization.agents.specialists.network_client import NetworkClientAgent0804 as NetworkClientAgent0804  # noqa: F401

__all__ = ["NetworkClientAgent0804", "create"]


def create(hub, aid: int = 804):
    """The historical create() — kept with the same default."""
    return NetworkClientAgent0804(name="network_client_0804", hub=hub, agent_id=aid)
