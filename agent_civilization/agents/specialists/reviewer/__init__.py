# reviewer agents — 50 agents, GENERATED (R-105 / D-049)
# The 50 classes are generated at import time from the single template body in
# _templates/reviewer_template.py via the factory — same names, same __all__,
# same agent_id/create() defaults as the historical 50 files (which remain as
# thin compatibility shims re-exporting the generated classes).
from agent_civilization.agents.specialists._templates._factory import generate_spec_classes

_classes = generate_spec_classes("reviewer")
for _name in _classes:
    globals()[_name] = _classes[_name]

__all__ = [
    "ReviewerAgent0009",
    "ReviewerAgent0029",
    "ReviewerAgent0049",
    "ReviewerAgent0069",
    "ReviewerAgent0089",
    "ReviewerAgent0109",
    "ReviewerAgent0129",
    "ReviewerAgent0149",
    "ReviewerAgent0169",
    "ReviewerAgent0189",
    "ReviewerAgent0209",
    "ReviewerAgent0229",
    "ReviewerAgent0249",
    "ReviewerAgent0269",
    "ReviewerAgent0289",
    "ReviewerAgent0309",
    "ReviewerAgent0329",
    "ReviewerAgent0349",
    "ReviewerAgent0369",
    "ReviewerAgent0389",
    "ReviewerAgent0409",
    "ReviewerAgent0429",
    "ReviewerAgent0449",
    "ReviewerAgent0469",
    "ReviewerAgent0489",
    "ReviewerAgent0509",
    "ReviewerAgent0529",
    "ReviewerAgent0549",
    "ReviewerAgent0569",
    "ReviewerAgent0589",
    "ReviewerAgent0609",
    "ReviewerAgent0629",
    "ReviewerAgent0649",
    "ReviewerAgent0669",
    "ReviewerAgent0689",
    "ReviewerAgent0709",
    "ReviewerAgent0729",
    "ReviewerAgent0749",
    "ReviewerAgent0769",
    "ReviewerAgent0789",
    "ReviewerAgent0809",
    "ReviewerAgent0829",
    "ReviewerAgent0849",
    "ReviewerAgent0869",
    "ReviewerAgent0889",
    "ReviewerAgent0909",
    "ReviewerAgent0929",
    "ReviewerAgent0949",
    "ReviewerAgent0969",
    "ReviewerAgent0989",
]
