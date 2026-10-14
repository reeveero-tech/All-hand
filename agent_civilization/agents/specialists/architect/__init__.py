# architect agents — 50 agents, GENERATED (R-105 / D-049)
# The 50 classes are generated at import time from the single template body in
# _templates/architect_template.py via the factory — same names, same __all__,
# same agent_id/create() defaults as the historical 50 files (which remain as
# thin compatibility shims re-exporting the generated classes).
from agent_civilization.agents.specialists._templates._factory import generate_spec_classes

_classes = generate_spec_classes("architect")
for _name in _classes:
    globals()[_name] = _classes[_name]

__all__ = [
    "ArchitectAgent0014",
    "ArchitectAgent0034",
    "ArchitectAgent0054",
    "ArchitectAgent0074",
    "ArchitectAgent0094",
    "ArchitectAgent0114",
    "ArchitectAgent0134",
    "ArchitectAgent0154",
    "ArchitectAgent0174",
    "ArchitectAgent0194",
    "ArchitectAgent0214",
    "ArchitectAgent0234",
    "ArchitectAgent0254",
    "ArchitectAgent0274",
    "ArchitectAgent0294",
    "ArchitectAgent0314",
    "ArchitectAgent0334",
    "ArchitectAgent0354",
    "ArchitectAgent0374",
    "ArchitectAgent0394",
    "ArchitectAgent0414",
    "ArchitectAgent0434",
    "ArchitectAgent0454",
    "ArchitectAgent0474",
    "ArchitectAgent0494",
    "ArchitectAgent0514",
    "ArchitectAgent0534",
    "ArchitectAgent0554",
    "ArchitectAgent0574",
    "ArchitectAgent0594",
    "ArchitectAgent0614",
    "ArchitectAgent0634",
    "ArchitectAgent0654",
    "ArchitectAgent0674",
    "ArchitectAgent0694",
    "ArchitectAgent0714",
    "ArchitectAgent0734",
    "ArchitectAgent0754",
    "ArchitectAgent0774",
    "ArchitectAgent0794",
    "ArchitectAgent0814",
    "ArchitectAgent0834",
    "ArchitectAgent0854",
    "ArchitectAgent0874",
    "ArchitectAgent0894",
    "ArchitectAgent0914",
    "ArchitectAgent0934",
    "ArchitectAgent0954",
    "ArchitectAgent0974",
    "ArchitectAgent0994",
]
