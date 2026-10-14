#!/usr/bin/env python3
"""NetworkClientAgent0484 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single base class in
_templates/network_client_base.py; this historical module re-exports the generated
class (a type() subclass with the historical name and agent_id default) and
the create() function.
"""

from agent_civilization.agents.specialists.network_client import NetworkClientAgent0484 as NetworkClientAgent0484  # noqa: F401
from agent_civilization.agents.specialists._templates._factory import generate_create

__all__ = ["NetworkClientAgent0484", "create"]

create = generate_create("network_client", NetworkClientAgent0484, 484)
