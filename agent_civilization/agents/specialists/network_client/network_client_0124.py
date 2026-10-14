#!/usr/bin/env python3
"""NetworkClientAgent0124 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single template body in
_templates/network_client_template.py; this historical module now just re-exports the
generated class and the create() function so any existing importer keeps working.
"""

from agent_civilization.agents.specialists.network_client import NetworkClientAgent0124 as NetworkClientAgent0124  # noqa: F401

__all__ = ["NetworkClientAgent0124", "create"]


def create(hub, aid: int = 124):
    """The historical create() — kept with the same default."""
    return NetworkClientAgent0124(name="network_client_0124", hub=hub, agent_id=aid)
