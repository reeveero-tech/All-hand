#!/usr/bin/env python3
"""NetworkClientAgent0564 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single template body in
_templates/network_client_template.py; this historical module now just re-exports the
generated class and the create() function so any existing importer keeps working.
"""

from agent_civilization.agents.specialists.network_client import NetworkClientAgent0564 as NetworkClientAgent0564  # noqa: F401

__all__ = ["NetworkClientAgent0564", "create"]


def create(hub, aid: int = 564):
    """The historical create() — kept with the same default."""
    return NetworkClientAgent0564(name="network_client_0564", hub=hub, agent_id=aid)
