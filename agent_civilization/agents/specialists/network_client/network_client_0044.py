#!/usr/bin/env python3
"""NetworkClientAgent0044 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single template body in
_templates/network_client_template.py; this historical module now just re-exports the
generated class and the create() function so any existing importer keeps working.
"""

from agent_civilization.agents.specialists.network_client import NetworkClientAgent0044 as NetworkClientAgent0044  # noqa: F401

__all__ = ["NetworkClientAgent0044", "create"]


def create(hub, aid: int = 44):
    """The historical create() — kept with the same default."""
    return NetworkClientAgent0044(name="network_client_0044", hub=hub, agent_id=aid)
