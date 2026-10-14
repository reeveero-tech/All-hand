# surgeon agents — 50 agents, GENERATED (R-105 / D-049)
# The 50 classes are generated at import time from the single template body in
# _templates/surgeon_template.py via the factory — same names, same __all__,
# same agent_id/create() defaults as the historical 50 files (which remain as
# thin compatibility shims re-exporting the generated classes).
from agent_civilization.agents.specialists._templates._factory import generate_spec_classes

_classes = generate_spec_classes("surgeon")
for _name in _classes:
    globals()[_name] = _classes[_name]

__all__ = [
    "SurgeonAgent0012",
    "SurgeonAgent0032",
    "SurgeonAgent0052",
    "SurgeonAgent0072",
    "SurgeonAgent0092",
    "SurgeonAgent0112",
    "SurgeonAgent0132",
    "SurgeonAgent0152",
    "SurgeonAgent0172",
    "SurgeonAgent0192",
    "SurgeonAgent0212",
    "SurgeonAgent0232",
    "SurgeonAgent0252",
    "SurgeonAgent0272",
    "SurgeonAgent0292",
    "SurgeonAgent0312",
    "SurgeonAgent0332",
    "SurgeonAgent0352",
    "SurgeonAgent0372",
    "SurgeonAgent0392",
    "SurgeonAgent0412",
    "SurgeonAgent0432",
    "SurgeonAgent0452",
    "SurgeonAgent0472",
    "SurgeonAgent0492",
    "SurgeonAgent0512",
    "SurgeonAgent0532",
    "SurgeonAgent0552",
    "SurgeonAgent0572",
    "SurgeonAgent0592",
    "SurgeonAgent0612",
    "SurgeonAgent0632",
    "SurgeonAgent0652",
    "SurgeonAgent0672",
    "SurgeonAgent0692",
    "SurgeonAgent0712",
    "SurgeonAgent0732",
    "SurgeonAgent0752",
    "SurgeonAgent0772",
    "SurgeonAgent0792",
    "SurgeonAgent0812",
    "SurgeonAgent0832",
    "SurgeonAgent0852",
    "SurgeonAgent0872",
    "SurgeonAgent0892",
    "SurgeonAgent0912",
    "SurgeonAgent0932",
    "SurgeonAgent0952",
    "SurgeonAgent0972",
    "SurgeonAgent0992",
]
