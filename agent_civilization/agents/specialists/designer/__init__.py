# designer agents — 50 agents, GENERATED (R-105 / D-049)
# The 50 classes are generated at import time from the single template body in
# _templates/designer_template.py via the factory — same names, same __all__,
# same agent_id/create() defaults as the historical 50 files (which remain as
# thin compatibility shims re-exporting the generated classes).
from agent_civilization.agents.specialists._templates._factory import generate_spec_classes

_classes = generate_spec_classes("designer")
for _name in _classes:
    globals()[_name] = _classes[_name]

__all__ = [
    "DesignerAgent0007",
    "DesignerAgent0027",
    "DesignerAgent0047",
    "DesignerAgent0067",
    "DesignerAgent0087",
    "DesignerAgent0107",
    "DesignerAgent0127",
    "DesignerAgent0147",
    "DesignerAgent0167",
    "DesignerAgent0187",
    "DesignerAgent0207",
    "DesignerAgent0227",
    "DesignerAgent0247",
    "DesignerAgent0267",
    "DesignerAgent0287",
    "DesignerAgent0307",
    "DesignerAgent0327",
    "DesignerAgent0347",
    "DesignerAgent0367",
    "DesignerAgent0387",
    "DesignerAgent0407",
    "DesignerAgent0427",
    "DesignerAgent0447",
    "DesignerAgent0467",
    "DesignerAgent0487",
    "DesignerAgent0507",
    "DesignerAgent0527",
    "DesignerAgent0547",
    "DesignerAgent0567",
    "DesignerAgent0587",
    "DesignerAgent0607",
    "DesignerAgent0627",
    "DesignerAgent0647",
    "DesignerAgent0667",
    "DesignerAgent0687",
    "DesignerAgent0707",
    "DesignerAgent0727",
    "DesignerAgent0747",
    "DesignerAgent0767",
    "DesignerAgent0787",
    "DesignerAgent0807",
    "DesignerAgent0827",
    "DesignerAgent0847",
    "DesignerAgent0867",
    "DesignerAgent0887",
    "DesignerAgent0907",
    "DesignerAgent0927",
    "DesignerAgent0947",
    "DesignerAgent0967",
    "DesignerAgent0987",
]
