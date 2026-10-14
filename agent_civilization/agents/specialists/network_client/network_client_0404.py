#!/usr/bin/env python3
"""NetworkClientAgent0404 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single template body in
_templates/network_client_template.py; this historical module now just re-exports the
generated class and the create() function so any existing importer keeps working.
"""

from agent_civilization.agents.specialists.network_client import NetworkClientAgent0404 as NetworkClientAgent0404  # noqa: F401

__all__ = ["NetworkClientAgent0404", "create"]


def create(hub, aid: int = 404):
    """The historical create() — kept with the same default."""
    return NetworkClientAgent0404(name="network_client_0404", hub=hub, agent_id=aid)
