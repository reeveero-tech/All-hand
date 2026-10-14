# self_developer agents — 50 agents, GENERATED (R-105 / D-049)
# The 50 classes are generated at import time from the single template body in
# _templates/self_developer_template.py via the factory — same names, same __all__,
# same agent_id/create() defaults as the historical 50 files (which remain as
# thin compatibility shims re-exporting the generated classes).
from agent_civilization.agents.specialists._templates._factory import generate_spec_classes

_classes = generate_spec_classes("self_developer")
for _name in _classes:
    globals()[_name] = _classes[_name]

__all__ = [
    "SelfDeveloperAgent0013",
    "SelfDeveloperAgent0033",
    "SelfDeveloperAgent0053",
    "SelfDeveloperAgent0073",
    "SelfDeveloperAgent0093",
    "SelfDeveloperAgent0113",
    "SelfDeveloperAgent0133",
    "SelfDeveloperAgent0153",
    "SelfDeveloperAgent0173",
    "SelfDeveloperAgent0193",
    "SelfDeveloperAgent0213",
    "SelfDeveloperAgent0233",
    "SelfDeveloperAgent0253",
    "SelfDeveloperAgent0273",
    "SelfDeveloperAgent0293",
    "SelfDeveloperAgent0313",
    "SelfDeveloperAgent0333",
    "SelfDeveloperAgent0353",
    "SelfDeveloperAgent0373",
    "SelfDeveloperAgent0393",
    "SelfDeveloperAgent0413",
    "SelfDeveloperAgent0433",
    "SelfDeveloperAgent0453",
    "SelfDeveloperAgent0473",
    "SelfDeveloperAgent0493",
    "SelfDeveloperAgent0513",
    "SelfDeveloperAgent0533",
    "SelfDeveloperAgent0553",
    "SelfDeveloperAgent0573",
    "SelfDeveloperAgent0593",
    "SelfDeveloperAgent0613",
    "SelfDeveloperAgent0633",
    "SelfDeveloperAgent0653",
    "SelfDeveloperAgent0673",
    "SelfDeveloperAgent0693",
    "SelfDeveloperAgent0713",
    "SelfDeveloperAgent0733",
    "SelfDeveloperAgent0753",
    "SelfDeveloperAgent0773",
    "SelfDeveloperAgent0793",
    "SelfDeveloperAgent0813",
    "SelfDeveloperAgent0833",
    "SelfDeveloperAgent0853",
    "SelfDeveloperAgent0873",
    "SelfDeveloperAgent0893",
    "SelfDeveloperAgent0913",
    "SelfDeveloperAgent0933",
    "SelfDeveloperAgent0953",
    "SelfDeveloperAgent0973",
    "SelfDeveloperAgent0993",
]
