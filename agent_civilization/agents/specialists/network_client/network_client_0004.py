#!/usr/bin/env python3
"""NetworkClientAgent0004 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single base class in
_templates/network_client_base.py; this historical module re-exports the generated
class (a type() subclass with the historical name and agent_id default) and
the create() function.
"""

from agent_civilization.agents.specialists.network_client import NetworkClientAgent0004 as NetworkClientAgent0004  # noqa: F401
from agent_civilization.agents.specialists._templates._factory import generate_create

__all__ = ["NetworkClientAgent0004", "create"]

create = generate_create("network_client", NetworkClientAgent0004, 4)
