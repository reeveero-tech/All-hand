#!/usr/bin/env python3
"""NetworkClientAgent0384 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single template body in
_templates/network_client_template.py; this historical module now just re-exports the
generated class and the create() function so any existing importer keeps working.
"""

from agent_civilization.agents.specialists.network_client import NetworkClientAgent0384 as NetworkClientAgent0384  # noqa: F401

__all__ = ["NetworkClientAgent0384", "create"]


def create(hub, aid: int = 384):
    """The historical create() — kept with the same default."""
    return NetworkClientAgent0384(name="network_client_0384", hub=hub, agent_id=aid)
