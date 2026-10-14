# communicator agents — 50 agents, GENERATED (R-105 / D-049)
# The 50 classes are generated at import time from the single template body in
# _templates/communicator_template.py via the factory — same names, same __all__,
# same agent_id/create() defaults as the historical 50 files (which remain as
# thin compatibility shims re-exporting the generated classes).
from agent_civilization.agents.specialists._templates._factory import generate_spec_classes

_classes = generate_spec_classes("communicator")
for _name in _classes:
    globals()[_name] = _classes[_name]

__all__ = [
    "CommunicatorAgent0018",
    "CommunicatorAgent0038",
    "CommunicatorAgent0058",
    "CommunicatorAgent0078",
    "CommunicatorAgent0098",
    "CommunicatorAgent0118",
    "CommunicatorAgent0138",
    "CommunicatorAgent0158",
    "CommunicatorAgent0178",
    "CommunicatorAgent0198",
    "CommunicatorAgent0218",
    "CommunicatorAgent0238",
    "CommunicatorAgent0258",
    "CommunicatorAgent0278",
    "CommunicatorAgent0298",
    "CommunicatorAgent0318",
    "CommunicatorAgent0338",
    "CommunicatorAgent0358",
    "CommunicatorAgent0378",
    "CommunicatorAgent0398",
    "CommunicatorAgent0418",
    "CommunicatorAgent0438",
    "CommunicatorAgent0458",
    "CommunicatorAgent0478",
    "CommunicatorAgent0498",
    "CommunicatorAgent0518",
    "CommunicatorAgent0538",
    "CommunicatorAgent0558",
    "CommunicatorAgent0578",
    "CommunicatorAgent0598",
    "CommunicatorAgent0618",
    "CommunicatorAgent0638",
    "CommunicatorAgent0658",
    "CommunicatorAgent0678",
    "CommunicatorAgent0698",
    "CommunicatorAgent0718",
    "CommunicatorAgent0738",
    "CommunicatorAgent0758",
    "CommunicatorAgent0778",
    "CommunicatorAgent0798",
    "CommunicatorAgent0818",
    "CommunicatorAgent0838",
    "CommunicatorAgent0858",
    "CommunicatorAgent0878",
    "CommunicatorAgent0898",
    "CommunicatorAgent0918",
    "CommunicatorAgent0938",
    "CommunicatorAgent0958",
    "CommunicatorAgent0978",
    "CommunicatorAgent0998",
]
