# educator agents — 50 agents, GENERATED (R-105 / D-049)
# The 50 classes are generated at import time from the single template body in
# _templates/educator_template.py via the factory — same names, same __all__,
# same agent_id/create() defaults as the historical 50 files (which remain as
# thin compatibility shims re-exporting the generated classes).
from agent_civilization.agents.specialists._templates._factory import generate_spec_classes

_classes = generate_spec_classes("educator")
for _name in _classes:
    globals()[_name] = _classes[_name]

__all__ = [
    "EducatorAgent0010",
    "EducatorAgent0030",
    "EducatorAgent0050",
    "EducatorAgent0070",
    "EducatorAgent0090",
    "EducatorAgent0110",
    "EducatorAgent0130",
    "EducatorAgent0150",
    "EducatorAgent0170",
    "EducatorAgent0190",
    "EducatorAgent0210",
    "EducatorAgent0230",
    "EducatorAgent0250",
    "EducatorAgent0270",
    "EducatorAgent0290",
    "EducatorAgent0310",
    "EducatorAgent0330",
    "EducatorAgent0350",
    "EducatorAgent0370",
    "EducatorAgent0390",
    "EducatorAgent0410",
    "EducatorAgent0430",
    "EducatorAgent0450",
    "EducatorAgent0470",
    "EducatorAgent0490",
    "EducatorAgent0510",
    "EducatorAgent0530",
    "EducatorAgent0550",
    "EducatorAgent0570",
    "EducatorAgent0590",
    "EducatorAgent0610",
    "EducatorAgent0630",
    "EducatorAgent0650",
    "EducatorAgent0670",
    "EducatorAgent0690",
    "EducatorAgent0710",
    "EducatorAgent0730",
    "EducatorAgent0750",
    "EducatorAgent0770",
    "EducatorAgent0790",
    "EducatorAgent0810",
    "EducatorAgent0830",
    "EducatorAgent0850",
    "EducatorAgent0870",
    "EducatorAgent0890",
    "EducatorAgent0910",
    "EducatorAgent0930",
    "EducatorAgent0950",
    "EducatorAgent0970",
    "EducatorAgent0990",
]
