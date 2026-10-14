# monitor agents — 50 agents, GENERATED (R-105 / D-049)
# The 50 classes are generated at import time from the single template body in
# _templates/monitor_template.py via the factory — same names, same __all__,
# same agent_id/create() defaults as the historical 50 files (which remain as
# thin compatibility shims re-exporting the generated classes).
from agent_civilization.agents.specialists._templates._factory import generate_spec_classes

_classes = generate_spec_classes("monitor")
for _name in _classes:
    globals()[_name] = _classes[_name]

__all__ = [
    "MonitorAgent0008",
    "MonitorAgent0028",
    "MonitorAgent0048",
    "MonitorAgent0068",
    "MonitorAgent0088",
    "MonitorAgent0108",
    "MonitorAgent0128",
    "MonitorAgent0148",
    "MonitorAgent0168",
    "MonitorAgent0188",
    "MonitorAgent0208",
    "MonitorAgent0228",
    "MonitorAgent0248",
    "MonitorAgent0268",
    "MonitorAgent0288",
    "MonitorAgent0308",
    "MonitorAgent0328",
    "MonitorAgent0348",
    "MonitorAgent0368",
    "MonitorAgent0388",
    "MonitorAgent0408",
    "MonitorAgent0428",
    "MonitorAgent0448",
    "MonitorAgent0468",
    "MonitorAgent0488",
    "MonitorAgent0508",
    "MonitorAgent0528",
    "MonitorAgent0548",
    "MonitorAgent0568",
    "MonitorAgent0588",
    "MonitorAgent0608",
    "MonitorAgent0628",
    "MonitorAgent0648",
    "MonitorAgent0668",
    "MonitorAgent0688",
    "MonitorAgent0708",
    "MonitorAgent0728",
    "MonitorAgent0748",
    "MonitorAgent0768",
    "MonitorAgent0788",
    "MonitorAgent0808",
    "MonitorAgent0828",
    "MonitorAgent0848",
    "MonitorAgent0868",
    "MonitorAgent0888",
    "MonitorAgent0908",
    "MonitorAgent0928",
    "MonitorAgent0948",
    "MonitorAgent0968",
    "MonitorAgent0988",
]
