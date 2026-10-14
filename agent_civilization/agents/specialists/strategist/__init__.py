# strategist agents — 50 agents, GENERATED (R-105 / D-049)
# The 50 classes are generated at import time from the single template body in
# _templates/strategist_template.py via the factory — same names, same __all__,
# same agent_id/create() defaults as the historical 50 files (which remain as
# thin compatibility shims re-exporting the generated classes).
from agent_civilization.agents.specialists._templates._factory import generate_spec_classes

_classes = generate_spec_classes("strategist")
for _name in _classes:
    globals()[_name] = _classes[_name]

__all__ = [
    "StrategistAgent0017",
    "StrategistAgent0037",
    "StrategistAgent0057",
    "StrategistAgent0077",
    "StrategistAgent0097",
    "StrategistAgent0117",
    "StrategistAgent0137",
    "StrategistAgent0157",
    "StrategistAgent0177",
    "StrategistAgent0197",
    "StrategistAgent0217",
    "StrategistAgent0237",
    "StrategistAgent0257",
    "StrategistAgent0277",
    "StrategistAgent0297",
    "StrategistAgent0317",
    "StrategistAgent0337",
    "StrategistAgent0357",
    "StrategistAgent0377",
    "StrategistAgent0397",
    "StrategistAgent0417",
    "StrategistAgent0437",
    "StrategistAgent0457",
    "StrategistAgent0477",
    "StrategistAgent0497",
    "StrategistAgent0517",
    "StrategistAgent0537",
    "StrategistAgent0557",
    "StrategistAgent0577",
    "StrategistAgent0597",
    "StrategistAgent0617",
    "StrategistAgent0637",
    "StrategistAgent0657",
    "StrategistAgent0677",
    "StrategistAgent0697",
    "StrategistAgent0717",
    "StrategistAgent0737",
    "StrategistAgent0757",
    "StrategistAgent0777",
    "StrategistAgent0797",
    "StrategistAgent0817",
    "StrategistAgent0837",
    "StrategistAgent0857",
    "StrategistAgent0877",
    "StrategistAgent0897",
    "StrategistAgent0917",
    "StrategistAgent0937",
    "StrategistAgent0957",
    "StrategistAgent0977",
    "StrategistAgent0997",
]
