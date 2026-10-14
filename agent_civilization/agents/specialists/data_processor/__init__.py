# data_processor agents — 50 agents, GENERATED (R-105 / D-049)
# The 50 classes are generated at import time from the single template body in
# _templates/data_processor_template.py via the factory — same names, same __all__,
# same agent_id/create() defaults as the historical 50 files (which remain as
# thin compatibility shims re-exporting the generated classes).
from agent_civilization.agents.specialists._templates._factory import generate_spec_classes

_classes = generate_spec_classes("data_processor")
for _name in _classes:
    globals()[_name] = _classes[_name]

__all__ = [
    "DataProcessorAgent0000",
    "DataProcessorAgent0020",
    "DataProcessorAgent0040",
    "DataProcessorAgent0060",
    "DataProcessorAgent0080",
    "DataProcessorAgent0100",
    "DataProcessorAgent0120",
    "DataProcessorAgent0140",
    "DataProcessorAgent0160",
    "DataProcessorAgent0180",
    "DataProcessorAgent0200",
    "DataProcessorAgent0220",
    "DataProcessorAgent0240",
    "DataProcessorAgent0260",
    "DataProcessorAgent0280",
    "DataProcessorAgent0300",
    "DataProcessorAgent0320",
    "DataProcessorAgent0340",
    "DataProcessorAgent0360",
    "DataProcessorAgent0380",
    "DataProcessorAgent0400",
    "DataProcessorAgent0420",
    "DataProcessorAgent0440",
    "DataProcessorAgent0460",
    "DataProcessorAgent0480",
    "DataProcessorAgent0500",
    "DataProcessorAgent0520",
    "DataProcessorAgent0540",
    "DataProcessorAgent0560",
    "DataProcessorAgent0580",
    "DataProcessorAgent0600",
    "DataProcessorAgent0620",
    "DataProcessorAgent0640",
    "DataProcessorAgent0660",
    "DataProcessorAgent0680",
    "DataProcessorAgent0700",
    "DataProcessorAgent0720",
    "DataProcessorAgent0740",
    "DataProcessorAgent0760",
    "DataProcessorAgent0780",
    "DataProcessorAgent0800",
    "DataProcessorAgent0820",
    "DataProcessorAgent0840",
    "DataProcessorAgent0860",
    "DataProcessorAgent0880",
    "DataProcessorAgent0900",
    "DataProcessorAgent0920",
    "DataProcessorAgent0940",
    "DataProcessorAgent0960",
    "DataProcessorAgent0980",
]
