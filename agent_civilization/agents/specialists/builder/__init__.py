# builder agents — 50 agents, GENERATED (R-105 / D-049)
# The 50 classes are generated at import time from the single template body in
# _templates/builder_template.py via the factory — same names, same __all__,
# same agent_id/create() defaults as the historical 50 files (which remain as
# thin compatibility shims re-exporting the generated classes).
from agent_civilization.agents.specialists._templates._factory import generate_spec_classes

_classes = generate_spec_classes("builder")
for _name in _classes:
    globals()[_name] = _classes[_name]

__all__ = [
    "BuilderAgent0006",
    "BuilderAgent0026",
    "BuilderAgent0046",
    "BuilderAgent0066",
    "BuilderAgent0086",
    "BuilderAgent0106",
    "BuilderAgent0126",
    "BuilderAgent0146",
    "BuilderAgent0166",
    "BuilderAgent0186",
    "BuilderAgent0206",
    "BuilderAgent0226",
    "BuilderAgent0246",
    "BuilderAgent0266",
    "BuilderAgent0286",
    "BuilderAgent0306",
    "BuilderAgent0326",
    "BuilderAgent0346",
    "BuilderAgent0366",
    "BuilderAgent0386",
    "BuilderAgent0406",
    "BuilderAgent0426",
    "BuilderAgent0446",
    "BuilderAgent0466",
    "BuilderAgent0486",
    "BuilderAgent0506",
    "BuilderAgent0526",
    "BuilderAgent0546",
    "BuilderAgent0566",
    "BuilderAgent0586",
    "BuilderAgent0606",
    "BuilderAgent0626",
    "BuilderAgent0646",
    "BuilderAgent0666",
    "BuilderAgent0686",
    "BuilderAgent0706",
    "BuilderAgent0726",
    "BuilderAgent0746",
    "BuilderAgent0766",
    "BuilderAgent0786",
    "BuilderAgent0806",
    "BuilderAgent0826",
    "BuilderAgent0846",
    "BuilderAgent0866",
    "BuilderAgent0886",
    "BuilderAgent0906",
    "BuilderAgent0926",
    "BuilderAgent0946",
    "BuilderAgent0966",
    "BuilderAgent0986",
]
