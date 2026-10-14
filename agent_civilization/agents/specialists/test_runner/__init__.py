# test_runner agents — 50 agents, GENERATED (R-105 / D-049)
# The 50 classes are generated at import time from the single template body in
# _templates/test_runner_template.py via the factory — same names, same __all__,
# same agent_id/create() defaults as the historical 50 files (which remain as
# thin compatibility shims re-exporting the generated classes).
from agent_civilization.agents.specialists._templates._factory import generate_spec_classes

_classes = generate_spec_classes("test_runner")
for _name in _classes:
    globals()[_name] = _classes[_name]

__all__ = [
    "TestRunnerAgent0005",
    "TestRunnerAgent0025",
    "TestRunnerAgent0045",
    "TestRunnerAgent0065",
    "TestRunnerAgent0085",
    "TestRunnerAgent0105",
    "TestRunnerAgent0125",
    "TestRunnerAgent0145",
    "TestRunnerAgent0165",
    "TestRunnerAgent0185",
    "TestRunnerAgent0205",
    "TestRunnerAgent0225",
    "TestRunnerAgent0245",
    "TestRunnerAgent0265",
    "TestRunnerAgent0285",
    "TestRunnerAgent0305",
    "TestRunnerAgent0325",
    "TestRunnerAgent0345",
    "TestRunnerAgent0365",
    "TestRunnerAgent0385",
    "TestRunnerAgent0405",
    "TestRunnerAgent0425",
    "TestRunnerAgent0445",
    "TestRunnerAgent0465",
    "TestRunnerAgent0485",
    "TestRunnerAgent0505",
    "TestRunnerAgent0525",
    "TestRunnerAgent0545",
    "TestRunnerAgent0565",
    "TestRunnerAgent0585",
    "TestRunnerAgent0605",
    "TestRunnerAgent0625",
    "TestRunnerAgent0645",
    "TestRunnerAgent0665",
    "TestRunnerAgent0685",
    "TestRunnerAgent0705",
    "TestRunnerAgent0725",
    "TestRunnerAgent0745",
    "TestRunnerAgent0765",
    "TestRunnerAgent0785",
    "TestRunnerAgent0805",
    "TestRunnerAgent0825",
    "TestRunnerAgent0845",
    "TestRunnerAgent0865",
    "TestRunnerAgent0885",
    "TestRunnerAgent0905",
    "TestRunnerAgent0925",
    "TestRunnerAgent0945",
    "TestRunnerAgent0965",
    "TestRunnerAgent0985",
]
