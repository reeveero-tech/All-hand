# researcher agents — 50 agents, GENERATED (R-105 / D-049)
# The 50 classes are generated at import time from the single template body in
# _templates/researcher_template.py via the factory — same names, same __all__,
# same agent_id/create() defaults as the historical 50 files (which remain as
# thin compatibility shims re-exporting the generated classes).
from agent_civilization.agents.specialists._templates._factory import generate_spec_classes

_classes = generate_spec_classes("researcher")
for _name in _classes:
    globals()[_name] = _classes[_name]

__all__ = [
    "ResearcherAgent0016",
    "ResearcherAgent0036",
    "ResearcherAgent0056",
    "ResearcherAgent0076",
    "ResearcherAgent0096",
    "ResearcherAgent0116",
    "ResearcherAgent0136",
    "ResearcherAgent0156",
    "ResearcherAgent0176",
    "ResearcherAgent0196",
    "ResearcherAgent0216",
    "ResearcherAgent0236",
    "ResearcherAgent0256",
    "ResearcherAgent0276",
    "ResearcherAgent0296",
    "ResearcherAgent0316",
    "ResearcherAgent0336",
    "ResearcherAgent0356",
    "ResearcherAgent0376",
    "ResearcherAgent0396",
    "ResearcherAgent0416",
    "ResearcherAgent0436",
    "ResearcherAgent0456",
    "ResearcherAgent0476",
    "ResearcherAgent0496",
    "ResearcherAgent0516",
    "ResearcherAgent0536",
    "ResearcherAgent0556",
    "ResearcherAgent0576",
    "ResearcherAgent0596",
    "ResearcherAgent0616",
    "ResearcherAgent0636",
    "ResearcherAgent0656",
    "ResearcherAgent0676",
    "ResearcherAgent0696",
    "ResearcherAgent0716",
    "ResearcherAgent0736",
    "ResearcherAgent0756",
    "ResearcherAgent0776",
    "ResearcherAgent0796",
    "ResearcherAgent0816",
    "ResearcherAgent0836",
    "ResearcherAgent0856",
    "ResearcherAgent0876",
    "ResearcherAgent0896",
    "ResearcherAgent0916",
    "ResearcherAgent0936",
    "ResearcherAgent0956",
    "ResearcherAgent0976",
    "ResearcherAgent0996",
]
