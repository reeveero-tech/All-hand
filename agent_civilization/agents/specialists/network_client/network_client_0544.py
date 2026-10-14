#!/usr/bin/env python3
"""NetworkClientAgent0544 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single template body in
_templates/network_client_template.py; this historical module now just re-exports the
generated class and the create() function so any existing importer keeps working.
"""

from agent_civilization.agents.specialists.network_client import NetworkClientAgent0544 as NetworkClientAgent0544  # noqa: F401

__all__ = ["NetworkClientAgent0544", "create"]


def create(hub, aid: int = 544):
    """The historical create() — kept with the same default."""
    return NetworkClientAgent0544(name="network_client_0544", hub=hub, agent_id=aid)
