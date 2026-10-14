#!/usr/bin/env python3
"""NetworkClientAgent0724 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single template body in
_templates/network_client_template.py; this historical module now just re-exports the
generated class and the create() function so any existing importer keeps working.
"""

from agent_civilization.agents.specialists.network_client import NetworkClientAgent0724 as NetworkClientAgent0724  # noqa: F401

__all__ = ["NetworkClientAgent0724", "create"]


def create(hub, aid: int = 724):
    """The historical create() — kept with the same default."""
    return NetworkClientAgent0724(name="network_client_0724", hub=hub, agent_id=aid)
