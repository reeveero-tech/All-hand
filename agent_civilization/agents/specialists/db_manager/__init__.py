# db_manager agents — 50 agents, GENERATED (R-105 / D-049)
# The 50 classes are generated at import time from the single template body in
# _templates/db_manager_template.py via the factory — same names, same __all__,
# same agent_id/create() defaults as the historical 50 files (which remain as
# thin compatibility shims re-exporting the generated classes).
from agent_civilization.agents.specialists._templates._factory import generate_spec_classes

_classes = generate_spec_classes("db_manager")
for _name in _classes:
    globals()[_name] = _classes[_name]

__all__ = [
    "DbManagerAgent0002",
    "DbManagerAgent0022",
    "DbManagerAgent0042",
    "DbManagerAgent0062",
    "DbManagerAgent0082",
    "DbManagerAgent0102",
    "DbManagerAgent0122",
    "DbManagerAgent0142",
    "DbManagerAgent0162",
    "DbManagerAgent0182",
    "DbManagerAgent0202",
    "DbManagerAgent0222",
    "DbManagerAgent0242",
    "DbManagerAgent0262",
    "DbManagerAgent0282",
    "DbManagerAgent0302",
    "DbManagerAgent0322",
    "DbManagerAgent0342",
    "DbManagerAgent0362",
    "DbManagerAgent0382",
    "DbManagerAgent0402",
    "DbManagerAgent0422",
    "DbManagerAgent0442",
    "DbManagerAgent0462",
    "DbManagerAgent0482",
    "DbManagerAgent0502",
    "DbManagerAgent0522",
    "DbManagerAgent0542",
    "DbManagerAgent0562",
    "DbManagerAgent0582",
    "DbManagerAgent0602",
    "DbManagerAgent0622",
    "DbManagerAgent0642",
    "DbManagerAgent0662",
    "DbManagerAgent0682",
    "DbManagerAgent0702",
    "DbManagerAgent0722",
    "DbManagerAgent0742",
    "DbManagerAgent0762",
    "DbManagerAgent0782",
    "DbManagerAgent0802",
    "DbManagerAgent0822",
    "DbManagerAgent0842",
    "DbManagerAgent0862",
    "DbManagerAgent0882",
    "DbManagerAgent0902",
    "DbManagerAgent0922",
    "DbManagerAgent0942",
    "DbManagerAgent0962",
    "DbManagerAgent0982",
]
