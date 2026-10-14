# analyst agents — 50 agents, GENERATED (R-105 / D-049)
# The 50 classes are generated at import time from the single template body in
# _templates/analyst_template.py via the factory — same names, same __all__,
# same agent_id/create() defaults as the historical 50 files (which remain as
# thin compatibility shims re-exporting the generated classes).
from agent_civilization.agents.specialists._templates._factory import generate_spec_classes

_classes = generate_spec_classes("analyst")
for _name in _classes:
    globals()[_name] = _classes[_name]

__all__ = [
    "AnalystAgent0015",
    "AnalystAgent0035",
    "AnalystAgent0055",
    "AnalystAgent0075",
    "AnalystAgent0095",
    "AnalystAgent0115",
    "AnalystAgent0135",
    "AnalystAgent0155",
    "AnalystAgent0175",
    "AnalystAgent0195",
    "AnalystAgent0215",
    "AnalystAgent0235",
    "AnalystAgent0255",
    "AnalystAgent0275",
    "AnalystAgent0295",
    "AnalystAgent0315",
    "AnalystAgent0335",
    "AnalystAgent0355",
    "AnalystAgent0375",
    "AnalystAgent0395",
    "AnalystAgent0415",
    "AnalystAgent0435",
    "AnalystAgent0455",
    "AnalystAgent0475",
    "AnalystAgent0495",
    "AnalystAgent0515",
    "AnalystAgent0535",
    "AnalystAgent0555",
    "AnalystAgent0575",
    "AnalystAgent0595",
    "AnalystAgent0615",
    "AnalystAgent0635",
    "AnalystAgent0655",
    "AnalystAgent0675",
    "AnalystAgent0695",
    "AnalystAgent0715",
    "AnalystAgent0735",
    "AnalystAgent0755",
    "AnalystAgent0775",
    "AnalystAgent0795",
    "AnalystAgent0815",
    "AnalystAgent0835",
    "AnalystAgent0855",
    "AnalystAgent0875",
    "AnalystAgent0895",
    "AnalystAgent0915",
    "AnalystAgent0935",
    "AnalystAgent0955",
    "AnalystAgent0975",
    "AnalystAgent0995",
]
