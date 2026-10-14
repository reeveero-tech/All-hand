# modifier agents — 50 agents, GENERATED (R-105 / D-049)
# The 50 classes are generated at import time from the single template body in
# _templates/modifier_template.py via the factory — same names, same __all__,
# same agent_id/create() defaults as the historical 50 files (which remain as
# thin compatibility shims re-exporting the generated classes).
from agent_civilization.agents.specialists._templates._factory import generate_spec_classes

_classes = generate_spec_classes("modifier")
for _name in _classes:
    globals()[_name] = _classes[_name]

__all__ = [
    "ModifierAgent0011",
    "ModifierAgent0031",
    "ModifierAgent0051",
    "ModifierAgent0071",
    "ModifierAgent0091",
    "ModifierAgent0111",
    "ModifierAgent0131",
    "ModifierAgent0151",
    "ModifierAgent0171",
    "ModifierAgent0191",
    "ModifierAgent0211",
    "ModifierAgent0231",
    "ModifierAgent0251",
    "ModifierAgent0271",
    "ModifierAgent0291",
    "ModifierAgent0311",
    "ModifierAgent0331",
    "ModifierAgent0351",
    "ModifierAgent0371",
    "ModifierAgent0391",
    "ModifierAgent0411",
    "ModifierAgent0431",
    "ModifierAgent0451",
    "ModifierAgent0471",
    "ModifierAgent0491",
    "ModifierAgent0511",
    "ModifierAgent0531",
    "ModifierAgent0551",
    "ModifierAgent0571",
    "ModifierAgent0591",
    "ModifierAgent0611",
    "ModifierAgent0631",
    "ModifierAgent0651",
    "ModifierAgent0671",
    "ModifierAgent0691",
    "ModifierAgent0711",
    "ModifierAgent0731",
    "ModifierAgent0751",
    "ModifierAgent0771",
    "ModifierAgent0791",
    "ModifierAgent0811",
    "ModifierAgent0831",
    "ModifierAgent0851",
    "ModifierAgent0871",
    "ModifierAgent0891",
    "ModifierAgent0911",
    "ModifierAgent0931",
    "ModifierAgent0951",
    "ModifierAgent0971",
    "ModifierAgent0991",
]
