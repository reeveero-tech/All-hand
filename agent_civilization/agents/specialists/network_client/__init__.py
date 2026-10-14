# network_client agents — 50 agents, GENERATED (R-105 / D-049)
# The 50 classes are generated at import time from the single template body in
# _templates/network_client_template.py via the factory — same names, same __all__,
# same agent_id/create() defaults as the historical 50 files (which remain as
# thin compatibility shims re-exporting the generated classes).
from agent_civilization.agents.specialists._templates._factory import generate_spec_classes

_classes = generate_spec_classes("network_client")
for _name in _classes:
    globals()[_name] = _classes[_name]

__all__ = [
    "NetworkClientAgent0004",
    "NetworkClientAgent0024",
    "NetworkClientAgent0044",
    "NetworkClientAgent0064",
    "NetworkClientAgent0084",
    "NetworkClientAgent0104",
    "NetworkClientAgent0124",
    "NetworkClientAgent0144",
    "NetworkClientAgent0164",
    "NetworkClientAgent0184",
    "NetworkClientAgent0204",
    "NetworkClientAgent0224",
    "NetworkClientAgent0244",
    "NetworkClientAgent0264",
    "NetworkClientAgent0284",
    "NetworkClientAgent0304",
    "NetworkClientAgent0324",
    "NetworkClientAgent0344",
    "NetworkClientAgent0364",
    "NetworkClientAgent0384",
    "NetworkClientAgent0404",
    "NetworkClientAgent0424",
    "NetworkClientAgent0444",
    "NetworkClientAgent0464",
    "NetworkClientAgent0484",
    "NetworkClientAgent0504",
    "NetworkClientAgent0524",
    "NetworkClientAgent0544",
    "NetworkClientAgent0564",
    "NetworkClientAgent0584",
    "NetworkClientAgent0604",
    "NetworkClientAgent0624",
    "NetworkClientAgent0644",
    "NetworkClientAgent0664",
    "NetworkClientAgent0684",
    "NetworkClientAgent0704",
    "NetworkClientAgent0724",
    "NetworkClientAgent0744",
    "NetworkClientAgent0764",
    "NetworkClientAgent0784",
    "NetworkClientAgent0804",
    "NetworkClientAgent0824",
    "NetworkClientAgent0844",
    "NetworkClientAgent0864",
    "NetworkClientAgent0884",
    "NetworkClientAgent0904",
    "NetworkClientAgent0924",
    "NetworkClientAgent0944",
    "NetworkClientAgent0964",
    "NetworkClientAgent0984",
]
